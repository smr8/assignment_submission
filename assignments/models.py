import os

from django.conf import settings
from django.db import models


def submission_upload_to(instance, filename):
    """Upload files to: project_slug/filename"""
    project_slug = instance.project.slug
    return os.path.join(project_slug, filename)


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

    class Meta:
        verbose_name = "作业项目"
        verbose_name_plural = verbose_name
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


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