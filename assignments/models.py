import os

from django.conf import settings
from django.db import models
from django.utils import timezone


def submission_upload_to(instance, filename):
    """Upload files to: project_slug/filename"""
    project_slug = instance.project.slug
    return os.path.join(project_slug, filename)


class ProjectQuerySet(models.QuerySet):
    def active(self):
        """未归档的项目（正常使用中）"""
        return self.filter(archived_at__isnull=True)

    def archived(self):
        """已归档的项目（学生端隐藏，提交记录保留）"""
        return self.filter(archived_at__isnull=False)


class Project(models.Model):
    """作业项目 — 管理员在后台创建"""
    name = models.CharField("项目名称", max_length=128)
    slug = models.SlugField("标识", max_length=128, unique=True, help_text="用于文件目录命名")
    description = models.TextField("项目描述", blank=True)
    is_active = models.BooleanField("启用", default=True)

    # 自动重命名
    auto_rename = models.BooleanField("启用自动重命名", default=True)
    rename_pattern = models.CharField(
        "重命名正则模板",
        max_length=256,
        default="{student_id} {name}{ext}",
        help_text="可用变量: {student_id}, {name}, {project}, {timestamp}, {original_name}, {ext}",
    )

    # 存储位置
    storage_backend = models.CharField(
        "存储后端",
        max_length=32,
        default="local",
        choices=[
            ("local", "本地存储"),
            ("webdav", "WebDAV"),
            ("s3", "Amazon S3"),
            ("smb", "SMB 共享"),
        ],
    )

    # 时限
    deadline = models.DateTimeField("截止时间", null=True, blank=True)
    max_file_size = models.PositiveIntegerField("最大文件大小 (MB)", default=50)
    allowed_extensions = models.CharField(
        "允许的文件类型",
        max_length=512,
        blank=True,
        help_text="逗号分隔，如 .pdf,.docx,.zip，留空则允许所有",
    )

    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    # 归档：非空即已归档 —— 学生端不再显示、不再接收提交，提交记录与文件全部保留
    archived_at = models.DateTimeField(
        "归档时间",
        null=True,
        blank=True,
        db_index=True,
        help_text="非空表示已归档：学生端不可见且无法提交，可随时恢复",
    )

    objects = ProjectQuerySet.as_manager()

    class Meta:
        verbose_name = "作业项目"
        verbose_name_plural = verbose_name
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    @property
    def is_archived(self):
        return self.archived_at is not None


def project_image_upload_to(instance, filename):
    """作业相关图片: project_assets/<slug>/images/filename"""
    return os.path.join("project_assets", instance.project.slug, "images", filename)


def project_attachment_upload_to(instance, filename):
    """作业相关附件: project_assets/<slug>/files/filename"""
    return os.path.join("project_assets", instance.project.slug, "files", filename)


class ProjectImage(models.Model):
    """作业项目的配图（图片 + 描述，供学生提交页预览）"""
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="images", verbose_name="作业项目"
    )
    image = models.FileField("图片", upload_to=project_image_upload_to)
    caption = models.CharField("描述", max_length=255, blank=True)
    sort_order = models.PositiveIntegerField("排序", default=0)
    created_at = models.DateTimeField("上传时间", auto_now_add=True)

    class Meta:
        verbose_name = "作业图片"
        verbose_name_plural = verbose_name
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.caption or os.path.basename(self.image.name)


class ProjectAttachment(models.Model):
    """作业项目的附件（资料，供学生下载）"""
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="attachments", verbose_name="作业项目"
    )
    file = models.FileField("附件", upload_to=project_attachment_upload_to)
    name = models.CharField("显示名称", max_length=255, blank=True)
    file_size = models.PositiveIntegerField("文件大小 (bytes)", default=0)
    created_at = models.DateTimeField("上传时间", auto_now_add=True)

    class Meta:
        verbose_name = "作业附件"
        verbose_name_plural = verbose_name
        ordering = ["id"]

    def __str__(self):
        return self.name or os.path.basename(self.file.name)

    @property
    def display_name(self):
        return self.name or os.path.basename(self.file.name)


class Submission(models.Model):
    """学生提交的作业"""
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="submissions", verbose_name="作业项目")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="submissions", verbose_name="学生")
    file = models.FileField("作业文件", upload_to=submission_upload_to)
    original_filename = models.CharField("原始文件名", max_length=512)
    renamed_filename = models.CharField("重命名后文件名", max_length=512, blank=True)
    file_size = models.PositiveIntegerField("文件大小 (bytes)", default=0)
    auto_renamed = models.BooleanField("自动重命名", default=False)

    # 申诉
    appeal = models.TextField("申诉内容", blank=True)
    appeal_submitted = models.BooleanField("已提交申诉", default=False)

    submitted_at = models.DateTimeField("提交时间", auto_now_add=True)

    @property
    def is_late(self):
        """是否逾期提交：首次提交时间晚于项目截止时间"""
        if self.project.deadline and self.submitted_at:
            return self.submitted_at > self.project.deadline
        return False

    class Meta:
        verbose_name = "提交记录"
        verbose_name_plural = verbose_name
        ordering = ["-submitted_at"]
        unique_together = ["project", "student"]

    def __str__(self):
        return f"{self.student.name} - {self.project.name}"


class LiveSettings(models.Model):
    """系统全局设置（单例）"""
    # 备份
    backup_enabled = models.BooleanField("启用自动备份", default=False)
    backup_interval = models.PositiveIntegerField("备份间隔 (分钟)", default=60)
    auto_cleanup_enabled = models.BooleanField("启用自动清理", default=False)
    cleanup_after_backup = models.PositiveIntegerField("备份成功后清理本地存档 (天数)", default=7, help_text="0=立即清理，留空=不自动清理")

    # WebDAV
    webdav_url = models.CharField("WebDAV URL", max_length=512, blank=True)
    webdav_username = models.CharField("WebDAV 用户名", max_length=128, blank=True)
    webdav_password = models.CharField("WebDAV 密码", max_length=128, blank=True)

    # S3
    s3_endpoint = models.CharField("S3 Endpoint", max_length=512, blank=True)
    s3_access_key = models.CharField("S3 Access Key", max_length=256, blank=True)
    s3_secret_key = models.CharField("S3 Secret Key", max_length=256, blank=True)
    s3_bucket = models.CharField("S3 Bucket", max_length=256, blank=True)
    s3_region = models.CharField("S3 Region", max_length=128, blank=True, default="us-east-1")

    # SMB
    smb_host = models.CharField("SMB Host", max_length=256, blank=True)
    smb_share = models.CharField("SMB Share", max_length=256, blank=True)
    smb_username = models.CharField("SMB 用户名", max_length=128, blank=True)
    smb_password = models.CharField("SMB 密码", max_length=128, blank=True)
    smb_domain = models.CharField("SMB Domain", max_length=128, blank=True)

    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "系统设置"
        verbose_name_plural = verbose_name

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return "系统设置"


class BackupRecord(models.Model):
    """备份记录 — 每次执行备份后创建一条"""

    BACKUP_TYPES = [
        ("full", "全量备份"),
        ("incremental", "增量备份"),
    ]

    STATUSES = [
        ("success", "成功"),
        ("partial", "部分成功"),
        ("failed", "失败"),
    ]

    backup_type = models.CharField("备份类型", max_length=16, default="full", choices=BACKUP_TYPES)
    backend_type = models.CharField("存储后端", max_length=32, default="s3")
    status = models.CharField("状态", max_length=16, default="success", choices=STATUSES)
    backup_prefix = models.CharField("远程备份路径前缀", max_length=512, blank=True, help_text="如 backups/20250730_173000/")
    file_count = models.PositiveIntegerField("备份文件数", default=0)
    total_size = models.PositiveBigIntegerField("备份总大小 (bytes)", default=0)
    database_backed_up = models.BooleanField("已备份数据库", default=False)
    duration_seconds = models.FloatField("耗时 (秒)", default=0)
    error_message = models.TextField("错误信息", blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "备份记录"
        verbose_name_plural = verbose_name
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.status}] {self.backend_type} - {timezone.localtime(self.created_at).strftime('%Y-%m-%d %H:%M:%S')}"

    @property
    def total_size_display(self):
        """人类可读的文件大小"""
        size = self.total_size
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"