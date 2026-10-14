"""
Django 管理命令：全量备份到远程存储（S3 优先）和本地清理

备份策略:
  1. 将 SQLite 数据库文件上传到远程存储
  2. 将 media 目录下所有文件上传到远程存储
  3. 生成 manifest.json 清单文件
  4. 记录备份结果到 BackupRecord 模型

备份目录结构 (远程):
  backups/YYYYMMDD_HHMMSS/
  ├── manifest.json
  ├── db.sqlite3
  └── media/
      └── <project_slug>/<filename>
"""
import json
import logging
import os
import time
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from assignments.models import BackupRecord, LiveSettings, Project, Submission
from storage_backends import get_storage_backend

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "全量备份本地文件和数据库到远程存储（S3/WebDAV/SMB）并可选清理本地存档"

    def add_arguments(self, parser):
        parser.add_argument(
            "--backend",
            type=str,
            default="",
            help="指定备份使用的后端 (s3/webdav/smb)，不指定则自动选择已配置的后端",
        )
        parser.add_argument(
            "--skip-db",
            action="store_true",
            help="跳过数据库备份",
        )
        parser.add_argument(
            "--no-cleanup",
            action="store_true",
            help="本次备份后不执行自动清理",
        )

    def handle(self, *args, **options):
        s = LiveSettings.load()
        start_time = time.time()

        self.stdout.write("=" * 60)
        self.stdout.write(f"备份任务开始 — {timezone.localtime().strftime('%Y-%m-%d %H:%M:%S')}")
        self.stdout.write("=" * 60)

        # 确定备份后端
        backend_type = options["backend"] or self._select_backend(s)
        if not backend_type:
            reason = "没有可用的远程存储后端，请先在设置中配置 S3/WebDAV/SMB。"
            self.stdout.write(self.style.ERROR(f"❌ {reason}"))
            # 关键：失败也要落一条记录，否则「备份历史」看不到任何结果
            self._record_failure("", reason, start_time)
            return

        self.stdout.write(f"📦 使用存储后端: {backend_type}")

        try:
            remote_storage = get_storage_backend(backend_type, s)
        except Exception as e:
            reason = f"无法初始化存储后端: {e}"
            self.stdout.write(self.style.ERROR(f"❌ {reason}"))
            self._record_failure(backend_type, reason, start_time)
            return

        # 测试连接
        ok, msg = remote_storage.test_connection()
        if not ok:
            self.stdout.write(self.style.ERROR(f"❌ {msg}"))
            self._record_failure(backend_type, msg, start_time)
            return
        self.stdout.write(f"✅ {msg}")

        # 生成备份路径前缀
        timestamp = timezone.localtime().strftime("%Y%m%d_%H%M%S")
        backup_prefix = f"backups/{timestamp}/"

        file_count = 0
        total_size = 0
        errors = []
        db_backed_up = not options["skip_db"]

        # 1. 备份数据库
        if db_backed_up:
            count, size, db_errors = self._backup_database(remote_storage, backup_prefix)
            file_count += count
            total_size += size
            errors.extend(db_errors)

        # 2. 备份所有媒体文件
        media_count, media_size, media_errors = self._backup_media_files(remote_storage, backup_prefix)
        file_count += media_count
        total_size += media_size
        errors.extend(media_errors)

        # 3. 生成并上传 manifest
        manifest = self._create_manifest(timestamp, backend_type, file_count, total_size, db_backed_up, errors)
        try:
            manifest_local = os.path.join(settings.MEDIA_ROOT, f"_tmp_manifest_{timestamp}.json")
            os.makedirs(os.path.dirname(manifest_local), exist_ok=True)
            with open(manifest_local, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)
            remote_storage.upload(manifest_local, f"{backup_prefix}manifest.json")
            os.remove(manifest_local)
            file_count += 1
            self.stdout.write(f"  ✅ manifest.json")
        except Exception as e:
            errors.append(f"manifest 上传失败: {e}")
            self.stdout.write(self.style.ERROR(f"  ❌ manifest.json: {e}"))

        duration = time.time() - start_time

        # 4. 记录备份结果
        status = "success" if not errors else ("partial" if file_count > 0 else "failed")
        record = BackupRecord.objects.create(
            backup_type="full",
            backend_type=backend_type,
            status=status,
            backup_prefix=backup_prefix,
            file_count=file_count,
            total_size=total_size,
            database_backed_up=db_backed_up,
            duration_seconds=round(duration, 2),
            error_message="\n".join(errors) if errors else "",
        )

        self.stdout.write("")
        self.stdout.write("=" * 60)
        self.stdout.write(f"备份结果: {self._status_label(status)}")
        self.stdout.write(f"  文件数: {file_count}")
        self.stdout.write(f"  总大小: {record.total_size_display}")
        self.stdout.write(f"  耗时: {duration:.1f}s")
        self.stdout.write(f"  远程路径: {backup_prefix}")
        if errors:
            self.stdout.write(self.style.WARNING(f"  错误 ({len(errors)}):"))
            for e in errors[:10]:
                self.stdout.write(self.style.WARNING(f"    - {e}"))
        self.stdout.write("=" * 60)

        # 5. 自动清理
        if not options["no_cleanup"] and status != "failed" and s.auto_cleanup_enabled:
            self._cleanup_local_files(s)

    def _select_backend(self, settings_obj):
        """自动选择一个已配置的远程存储后端，优先 S3"""
        if settings_obj.s3_bucket and settings_obj.s3_access_key and settings_obj.s3_secret_key:
            return "s3"
        if settings_obj.webdav_url and settings_obj.webdav_username:
            return "webdav"
        if settings_obj.smb_host and settings_obj.smb_share:
            return "smb"
        return ""

    def _record_failure(self, backend_type, message, start_time):
        """备份在真正开始前就失败时，也要写一条 BackupRecord，
        这样「备份历史」页面才能反映真实结果（而不是永远没有新记录）。"""
        try:
            BackupRecord.objects.create(
                backup_type="full",
                backend_type=backend_type or "none",
                status="failed",
                backup_prefix="",
                file_count=0,
                total_size=0,
                database_backed_up=False,
                duration_seconds=round(time.time() - start_time, 2),
                error_message=message,
            )
        except Exception as exc:  # 记录失败不应掩盖原始错误
            self.stdout.write(self.style.ERROR(f"（写入备份记录失败: {exc}）"))

    def _backup_database(self, remote_storage, backup_prefix):
        """备份数据库文件"""
        count = 0
        size = 0
        errors = []

        db_path = settings.DATABASES["default"]["NAME"]
        db_path = str(db_path)

        if not os.path.exists(db_path):
            errors.append(f"数据库文件不存在: {db_path}")
            return count, size, errors

        self.stdout.write(f"\n🗄️  备份数据库: {db_path}")
        remote_key = f"{backup_prefix}db.sqlite3"
        try:
            remote_storage.upload(db_path, remote_key)
            size = os.path.getsize(db_path)
            count = 1
            self.stdout.write(f"  ✅ db.sqlite3 ({self._format_size(size)})")
        except Exception as e:
            errors.append(f"数据库备份失败: {e}")
            self.stdout.write(self.style.ERROR(f"  ❌ db.sqlite3: {e}"))

        return count, size, errors

    def _backup_media_files(self, remote_storage, backup_prefix):
        """备份 media 目录下所有文件"""
        count = 0
        size = 0
        errors = []

        media_root = str(settings.MEDIA_ROOT)
        if not os.path.exists(media_root):
            self.stdout.write("\n📁 media 目录不存在，跳过文件备份。")
            return count, size, errors

        self.stdout.write(f"\n📁 备份媒体文件: {media_root}")
        local_storage = get_storage_backend("local")

        for root, dirs, files in os.walk(media_root):
            for filename in files:
                local_path = os.path.join(root, filename)
                rel_path = os.path.relpath(local_path, media_root)
                # 统一使用正斜杠
                rel_path = rel_path.replace(os.sep, "/")
                remote_key = f"{backup_prefix}media/{rel_path}"

                try:
                    file_size = os.path.getsize(local_path)
                    remote_storage.upload(local_path, remote_key)
                    count += 1
                    size += file_size
                    self.stdout.write(f"  ✅ {rel_path} ({self._format_size(file_size)})")
                except Exception as e:
                    errors.append(f"{rel_path}: {e}")
                    self.stdout.write(self.style.ERROR(f"  ❌ {rel_path}: {e}"))

        return count, size, errors

    def _create_manifest(self, timestamp, backend_type, file_count, total_size, db_backed_up, errors):
        """创建备份清单"""
        return {
            "backup_time": timestamp,
            "backend_type": backend_type,
            "file_count": file_count,
            "total_size": total_size,
            "database_backed_up": db_backed_up,
            "django_version": self._get_django_version(),
            "errors": errors,
            "created_at": timezone.localtime().isoformat(),
        }

    def _get_django_version(self):
        import django
        return django.get_version()

    def _cleanup_local_files(self, settings_obj):
        """清理过期的本地文件"""
        days = settings_obj.cleanup_after_backup
        self.stdout.write(f"\n🗑️  开始清理本地存档 (策略: {days}天后清理)")

        if days == 0:
            self._delete_all_local()
        else:
            cutoff = timezone.now() - timedelta(days=days)
            old_submissions = Submission.objects.filter(submitted_at__lt=cutoff)
            count = 0
            for sub in old_submissions:
                if sub.file:
                    local_path = os.path.join(settings.MEDIA_ROOT, sub.file.name)
                    if os.path.exists(local_path):
                        try:
                            os.remove(local_path)
                            count += 1
                            self.stdout.write(f"  🗑️  {sub.file.name}")
                        except Exception as e:
                            self.stdout.write(self.style.ERROR(f"  ❌ {sub.file.name}: {e}"))
            self.stdout.write(f"清理完成，共删除 {count} 个文件。")

    def _delete_all_local(self):
        """删除所有本地提交文件（保留目录结构和数据库记录）"""
        media_root = str(settings.MEDIA_ROOT)
        if not os.path.exists(media_root):
            return

        count = 0
        for sub in Submission.objects.all():
            if sub.file:
                local_path = os.path.join(media_root, sub.file.name)
                if os.path.exists(local_path):
                    try:
                        os.remove(local_path)
                        count += 1
                    except Exception:
                        pass
        self.stdout.write(f"本地存档已清理，共删除 {count} 个提交文件（数据库记录已保留）。")

    def _status_label(self, status):
        return {"success": "✅ 全部成功", "partial": "⚠️ 部分成功", "failed": "❌ 失败"}.get(status, status)

    def _format_size(self, size):
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                return f"{size:.1f}{unit}"
            size /= 1024
        return f"{size:.1f}TB"
