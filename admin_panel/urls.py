from django.urls import path

from . import views

app_name = "admin_panel"

urlpatterns = [
    path("", views.dashboard_view, name="dashboard"),

    # 系统设置
    path("settings/", views.settings_view, name="settings"),

    # 统计与导出
    path("stats/", views.stats_view, name="stats"),
    path("export/csv/", views.export_csv_view, name="export_csv"),

    # 项目管理（自建 CRUD）
    path("projects/", views.project_list_view, name="project_list"),
    path("projects/create/", views.project_create_view, name="project_create"),
    path("projects/<int:pk>/edit/", views.project_edit_view, name="project_edit"),
    path("projects/<int:pk>/delete/", views.project_delete_view, name="project_delete"),

    # 提交记录管理
    path("submissions/", views.submission_list_view, name="submission_list"),
    path("submissions/<int:pk>/upload/", views.submission_upload_view, name="submission_upload_for"),
    path("submissions/<int:pk>/download/", views.download_submission_view, name="download_submission"),
    path("submissions/batch-download/", views.submission_batch_download_view, name="submission_batch_download"),

    # 用户管理（自建 CRUD）
    path("users/", views.user_list_view, name="user_list"),
    path("users/create/", views.user_create_view, name="user_create"),
    path("users/<int:pk>/edit/", views.user_edit_view, name="user_edit"),
    path("users/<int:pk>/delete/", views.user_delete_view, name="user_delete"),

    # 备份与清理
    path("backup/now/", views.backup_now_view, name="backup_now"),
    path("cleanup/now/", views.cleanup_now_view, name="cleanup_now"),
    path("check/backup/", views.check_backup_view, name="check_backup"),
]