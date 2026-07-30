"""
Django 管理命令：从远程备份恢复文件和数据库

用法:
  python manage.py restore_from_backup --list              # 列出可用备份
  python manage.py restore_from_backup --prefix backups/20250730_173000/  # 恢复指定备份
  python manage.py restore_from_backup --prefix backups/... --skip-db     # 仅恢复文件，不恢复数据库
"""
import json
import os
import tempfile

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from assignments.models import LiveSettings
from storage_backends import get_storage_backend


class Command(BaseCommand):
    help = "从远程存储的备份中恢复文件和数据库"

    def add_arguments(self, parser):
        parser.add_argument(
            "--list",
            action="store_true",
            help="列出远程存储中可用的备份",
        )
        parser.add_argument(
            "--prefix",
            type=str,
            default="",
            help="指定要恢复的备份前缀 (如 backups/20250730_173000/)",
        )
        parser.add_argument(
            "--backend",
            type=str,
            default="",
            help="指定后端 (s3/webdav/smb)，不指定则自动选择",
        )
        parser.add_argument(
            "--skip-db",
            action="store_true",
            help="跳过数据库恢复",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="仅预览将要恢复的文件，不实际下载",
        )

    def handle(self, *args, **options):
        s = LiveSettings.load()

        # 确定后端
        backend_type = options["backend"] or self._select_backend(s)
        if not backend_type:
            self.stdout.write(self.style.ERROR("❌ 没有可用的远程存储后端，请先配置。"))
            return

        try:
            remote_storage = get_storage_backend(backend_type, s)
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ 无法初始化存储后端: {e}"))
            return

        # 测试连接
        ok, msg = remote_storage.test_connection()
        if not ok:
            self.stdout.write(self.style.ERROR(f"❌ {msg}"))
            return
        self.stdout.write(f"✅ {msg}")

        # 列出备份
        if options["list"]:
            self._list_backups(remote_storage)
            return

        # 恢复指定备份
        prefix = options["prefix"]
        if not prefix:
            self.stdout.write(self.style.ERROR("❌ 请使用 --prefix 指定要恢复的备份，或使用 --list 查看可用备份。"))
            return

        self._restore_backup(remote_storage, prefix, options["skip_db"], options["dry_run"])

    def _select_backend(self, settings_obj):
        if settings_obj.s3_bucket and settings_obj.s3_access_key and settings_obj.s3_secret_key:
            return "s3"
        if settings_obj.webdav_url and settings_obj.webdav_username:
            return "webdav"
        if settings_obj.smb_host and settings_obj.smb_share:
            return "smb"
        return ""

    def _list_backups(self, remote_storage):
        """列出所有可用的备份"""
        self.stdout.write("\n📋 可用备份列表:")
        self.stdout.write("-" * 60)

        try:
            # 列出 backups/ 前缀下所有文件
            all_files = remote_storage.list_files_with_info("backups/")
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ 无法列出备份: {e}"))
            return

        # 按 manifest.json 聚合备份
        backups = {}
        for f in all_files:
            key = f["key"]
            if key.endswith("manifest.json"):
                # 提取备份前缀: backups/YYYYMMDD_HHMMSS/manifest.json -> backups/YYYYMMDD_HHMMSS/
                prefix = key.rsplit("manifest.json", 1)[0]
                backups[prefix] = {"manifest_key": key, "info": f}

        if not backups:
            self.stdout.write(self.style.WARNING("未找到任何备份（没有 manifest.json 文件）。"))
            return

        for prefix, data in sorted(backups.items(), key=lambda x: x[0], reverse=True):
            info = data["info"]
            modified = info.get("last_modified", "")
            if hasattr(modified, "strftime"):
                modified = modified.strftime("%Y-%m-%d %H:%M:%S")
            self.stdout.write(f"  📦 {prefix}")
            self.stdout.write(f"     时间: {modified}")

            # 尝试读取 manifest 获取详情
            manifest = self._fetch_manifest(remote_storage, data["manifest_key"])
            if manifest:
                self.stdout.write(f"     文件数: {manifest.get('file_count', '?')}")
                self.stdout.write(f"     数据库: {'是' if manifest.get('database_backed_up') else '否'}")
                if manifest.get("errors"):
                    self.stdout.write(self.style.WARNING(f"     ⚠️ 有 {len(manifest['errors'])} 个错误"))
            self.stdout.write("")

    def _fetch_manifest(self, remote_storage, manifest_key):
        """下载并解析 manifest.json"""
        try:
            tmp_dir = tempfile.mkdtemp()
            tmp_file = os.path.join(tmp_dir, "manifest.json")
            remote_storage.download(manifest_key, tmp_file)
            with open(tmp_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
        finally:
            try:
                if os.path.exists(tmp_file):
                    os.remove(tmp_file)
                os.rmdir(tmp_dir)
            except Exception:
                pass

    def _restore_backup(self, remote_storage, prefix, skip_db, dry_run):
        """从指定备份恢复"""
        # 确保前缀以 / 结尾
        if not prefix.endswith("/"):
            prefix += "/"

        self.stdout.write(f"\n🔄 恢复备份: {prefix}")
        if dry_run:
            self.stdout.write(self.style.WARNING("⚠️  DRY RUN 模式 — 仅预览，不实际下载"))

        # 获取备份文件列表
        all_files = remote_storage.list_files_with_info(prefix)
        if not all_files:
            self.stdout.write(self.style.ERROR(f"❌ 未找到备份文件: {prefix}"))
            return

        # 分类文件
        db_file = None
        media_files = []
        manifest_file = None

        for f in all_files:
            key = f["key"]
            if key.endswith("manifest.json"):
                manifest_file = f
            elif key.endswith("db.sqlite3"):
                db_file = f
            elif "/media/" in key:
                media_files.append(f)

        self.stdout.write(f"  数据库: {'有' if db_file else '无'}")
        self.stdout.write(f"  媒体文件: {len(media_files)} 个")
        if manifest_file:
            self.stdout.write(f"  清单: 有")

        if dry_run:
            self.stdout.write("\n📋 将恢复的文件:")
            if db_file and not skip_db:
                self.stdout.write(f"  🗄️  {db_file['key']} ({self._format_size(db_file['size'])})")
            for mf in media_files:
                self.stdout.write(f"  📄 {mf['key']} ({self._format_size(mf['size'])})")
            return

        # 确认
        self.stdout.write(self.style.WARNING("\n⚠️  恢复操作将覆盖本地文件！"))
        if not skip_db and db_file:
            self.stdout.write(self.style.WARNING("⚠️  数据库将被覆盖，建议先手动备份当前数据库！"))

        media_root = str(settings.MEDIA_ROOT)
        db_path = str(settings.DATABASES["default"]["NAME"])
        restored_count = 0
        errors = []

        # 恢复数据库
        if not skip_db and db_file:
            self.stdout.write(f"\n🗄️  恢复数据库...")
            # 先下载到临时文件，再替换
            tmp_db = os.path.join(tempfile.gettempdir(), "db_restore.sqlite3")
            try:
                remote_storage.download(db_file["key"], tmp_db)
                # 关闭数据库连接
                from django.db import connection
                connection.close()
                # 备份当前数据库
                if os.path.exists(db_path):
                    backup_db = db_path + ".pre_restore"
                    import shutil
                    shutil.copy2(db_path, backup_db)
                    self.stdout.write(f"  当前数据库已备份到: {backup_db}")
                # 替换
                import shutil
                shutil.move(tmp_db, db_path)
                restored_count += 1
                self.stdout.write(f"  ✅ 数据库已恢复")
            except Exception as e:
                errors.append(f"数据库恢复失败: {e}")
                self.stdout.write(self.style.ERROR(f"  ❌ 数据库恢复失败: {e}"))

        # 恢复媒体文件
        self.stdout.write(f"\n📁 恢复媒体文件...")
        for mf in media_files:
            key = mf["key"]
            # 从 key 中提取相对于 media 的路径
            # key 格式: backups/YYYYMMDD_HHMMSS/media/<rel_path>
            media_idx = key.find("/media/")
            if media_idx == -1:
                continue
            rel_path = key[media_idx + len("/media/"):]
            local_path = os.path.join(media_root, rel_path.replace("/", os.sep))

            try:
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                remote_storage.download(key, local_path)
                restored_count += 1
                self.stdout.write(f"  ✅ {rel_path}")
            except Exception as e:
                errors.append(f"{rel_path}: {e}")
                self.stdout.write(self.style.ERROR(f"  ❌ {rel_path}: {e}"))

        # 汇总
        self.stdout.write("\n" + "=" * 60)
        if errors:
            self.stdout.write(self.style.WARNING(f"恢复完成（{restored_count} 个成功，{len(errors)} 个失败）"))
            for e in errors[:10]:
                self.stdout.write(self.style.WARNING(f"  - {e}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"✅ 恢复完成，共恢复 {restored_count} 个文件"))
        self.stdout.write("=" * 60)

        if not skip_db and db_file:
            self.stdout.write(self.style.WARNING("\n⚠️  数据库已恢复，建议重启应用以使更改生效。"))

    def _format_size(self, size):
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                return f"{size:.1f}{unit}"
            size /= 1024
        return f"{size:.1f}TB"
