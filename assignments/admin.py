from django.contrib import admin

from .models import BackupRecord, LiveSettings, Project, Submission


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active", "archived_at", "auto_rename", "storage_backend", "deadline", "created_at")
    list_filter = ("is_active", "archived_at", "auto_rename", "storage_backend")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ("project", "student", "original_filename", "renamed_filename", "file_size", "appeal_submitted", "submitted_at")
    list_filter = ("project", "appeal_submitted", "submitted_at")
    search_fields = ("student__name", "student__student_id", "project__name")


@admin.register(BackupRecord)
class BackupRecordAdmin(admin.ModelAdmin):
    list_display = ("created_at", "backend_type", "status", "backup_type", "file_count", "total_size", "database_backed_up", "duration_seconds")
    list_filter = ("status", "backend_type", "backup_type", "database_backed_up")
    search_fields = ("backup_prefix", "error_message")
    readonly_fields = ("created_at",)
    ordering = ("-created_at",)


@admin.register(LiveSettings)
class LiveSettingsAdmin(admin.ModelAdmin):
    list_display = ("__str__", "backup_enabled", "backup_interval", "auto_cleanup_enabled", "updated_at")