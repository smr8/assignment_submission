import csv
import io
import json
import os
import zipfile
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import User
from assignments.models import BackupRecord, LiveSettings, Project, Submission
from assignments.views import format_rename_pattern
from storage_backends import get_storage_backend


# ==================== 仪表盘 ====================

@staff_member_required
def dashboard_view(request):
    now = timezone.now()

    total_projects = Project.objects.count()
    total_submissions = Submission.objects.count()
    total_students = User.objects.filter(is_staff=False, is_active=True).count()

    projects = Project.objects.all()
    project_bars = []
    rates = []
    for project in projects:
        submitted_count = project.submissions.count()
        percentage = round(submitted_count / total_students * 100, 1) if total_students else 0
        rates.append(percentage)
        project_bars.append({
            "project": project,
            "submitted_count": submitted_count,
            "total_students": total_students,
            "percentage": percentage,
            "is_expired": project.deadline and now > project.deadline,
        })

    avg_rate = round(sum(rates) / len(rates), 1) if rates else 0

    pending_review = Submission.objects.filter(appeal_submitted=True).count()
    overdue_reviews = Submission.objects.filter(
        appeal_submitted=True, submitted_at__lt=now - timedelta(days=7)
    ).count()

    recent_submissions = Submission.objects.select_related("project", "student").order_by("-submitted_at")[:8]

    return render(request, "admin_panel/dashboard.html", {
        "total_projects": total_projects,
        "total_submissions": total_submissions,
        "total_students": total_students,
        "avg_rate": avg_rate,
        "pending_review": pending_review,
        "overdue_reviews": overdue_reviews,
        "project_bars": project_bars,
        "recent_submissions": recent_submissions,
    })


# ==================== 系统设置 ====================

@staff_member_required
def settings_view(request):
    s = LiveSettings.load()

    if request.method == "POST":
        s.webdav_url = request.POST.get("webdav_url", "")
        s.webdav_username = request.POST.get("webdav_username", "")
        s.webdav_password = request.POST.get("webdav_password", "")
        s.s3_endpoint = request.POST.get("s3_endpoint", "")
        s.s3_access_key = request.POST.get("s3_access_key", "")
        s.s3_secret_key = request.POST.get("s3_secret_key", "")
        s.s3_bucket = request.POST.get("s3_bucket", "")
        s.s3_region = request.POST.get("s3_region", "us-east-1")
        s.smb_host = request.POST.get("smb_host", "")
        s.smb_share = request.POST.get("smb_share", "")
        s.smb_username = request.POST.get("smb_username", "")
        s.smb_password = request.POST.get("smb_password", "")
        s.smb_domain = request.POST.get("smb_domain", "")
        s.backup_enabled = request.POST.get("backup_enabled") == "on"
        s.backup_interval = int(request.POST.get("backup_interval", 60))
        s.auto_cleanup_enabled = request.POST.get("auto_cleanup_enabled") == "on"
        s.cleanup_after_backup = int(request.POST.get("cleanup_after_backup", 7))
        s.save()
        messages.success(request, "设置已保存！")
        return redirect("admin_panel:settings")

    return render(request, "admin_panel/settings.html", {"settings": s})


# ==================== 统计 ====================

@staff_member_required
def stats_view(request):
    projects = Project.objects.all()
    project_stats = []
    total_students = User.objects.filter(is_staff=False, is_active=True).count()

    for project in projects:
        submitted_count = Submission.objects.filter(project=project).count()
        percentage = round(submitted_count / total_students * 100, 1) if total_students > 0 else 0
        project_stats.append({
            "project": project,
            "submitted_count": submitted_count,
            "total_students": total_students,
            "percentage": percentage,
            "is_expired": project.deadline and timezone.now() > project.deadline,
        })

    return render(request, "admin_panel/stats.html", {"project_stats": project_stats})


# ==================== 导出 CSV ====================

@staff_member_required
def export_csv_view(request):
    response = HttpResponse(content_type="text/csv; charset=utf-8-sig")
    response["Content-Disposition"] = f'attachment; filename="submissions_{timezone.now().strftime("%Y%m%d")}.csv"'

    writer = csv.writer(response)
    writer.writerow(["学号", "姓名", "作业项目", "原始文件名", "重命名文件名", "文件大小(B)", "自动重命名", "有申诉", "提交时间"])

    submissions = Submission.objects.select_related("project", "student").all()
    for s in submissions:
        writer.writerow([
            s.student.student_id, s.student.name, s.project.name,
            s.original_filename, s.renamed_filename, s.file_size,
            "是" if s.auto_renamed else "否",
            "是" if s.appeal_submitted else "否",
            s.submitted_at.strftime("%Y-%m-%d %H:%M:%S"),
        ])
    return response


# ==================== 项目管理（自建 CRUD） ====================

@staff_member_required
def project_list_view(request):
    projects = Project.objects.all().order_by("-created_at")
    return render(request, "admin_panel/project_list.html", {"projects": projects})


@staff_member_required
def project_create_view(request):
    if request.method == "POST":
        project = Project(
            name=request.POST.get("name", ""),
            slug=request.POST.get("slug", ""),
            description=request.POST.get("description", ""),
            is_active=request.POST.get("is_active") == "on",
            auto_rename=request.POST.get("auto_rename") == "on",
            rename_pattern=request.POST.get("rename_pattern", "{student_id} {name}{ext}"),
            storage_backend=request.POST.get("storage_backend", "local"),
            max_file_size=int(request.POST.get("max_file_size", 50)),
            allowed_extensions=request.POST.get("allowed_extensions", ""),
        )
        deadline_str = request.POST.get("deadline", "")
        if deadline_str:
            from django.utils import timezone as tz
            project.deadline = tz.datetime.fromisoformat(deadline_str)
        project.save()
        messages.success(request, f"项目「{project.name}」已创建！")
        return redirect("admin_panel:project_list")

    return render(request, "admin_panel/project_form.html", {
        "title": "新建作业项目",
        "form_values": {
            "name": "",
            "slug": "",
            "description": "",
            "rename_pattern": "{student_id} {name}{ext}",
            "storage_backend": "local",
            "max_file_size": 50,
            "deadline": "",
            "allowed_extensions": "",
            "is_active": True,
            "auto_rename": True,
        },
    })


@staff_member_required
def project_edit_view(request, pk):
    project = get_object_or_404(Project, pk=pk)
    if request.method == "POST":
        project.name = request.POST.get("name", project.name)
        project.slug = request.POST.get("slug", project.slug)
        project.description = request.POST.get("description", project.description)
        project.is_active = request.POST.get("is_active") == "on"
        project.auto_rename = request.POST.get("auto_rename") == "on"
        project.rename_pattern = request.POST.get("rename_pattern", project.rename_pattern)
        project.storage_backend = request.POST.get("storage_backend", project.storage_backend)
        project.max_file_size = int(request.POST.get("max_file_size", project.max_file_size))
        project.allowed_extensions = request.POST.get("allowed_extensions", project.allowed_extensions)
        deadline_str = request.POST.get("deadline", "")
        if deadline_str:
            from django.utils import timezone as tz
            project.deadline = tz.datetime.fromisoformat(deadline_str)
        else:
            project.deadline = None
        project.save()
        messages.success(request, f"项目「{project.name}」已更新！")
        return redirect("admin_panel:project_list")

    return render(request, "admin_panel/project_form.html", {
        "title": f"编辑：{project.name}",
        "form_values": {
            "name": project.name,
            "slug": project.slug,
            "description": project.description or "",
            "rename_pattern": project.rename_pattern,
            "storage_backend": project.storage_backend,
            "max_file_size": project.max_file_size,
            "deadline": project.deadline.strftime("%Y-%m-%dT%H:%M") if project.deadline else "",
            "allowed_extensions": project.allowed_extensions or "",
            "is_active": project.is_active,
            "auto_rename": project.auto_rename,
        },
    })


@staff_member_required
def project_delete_view(request, pk):
    project = get_object_or_404(Project, pk=pk)
    name = project.name
    project.delete()
    messages.success(request, f"项目「{name}」已删除！")
    return redirect("admin_panel:project_list")


@staff_member_required
def submission_list_view(request):
    filter_projects = Project.objects.all()
    project_id = request.GET.get("project", "")
    search = request.GET.get("search", "")
    appeal = request.GET.get("appeal", "")
    status = request.GET.get("status", "")

    if project_id:
        # 矩阵模式：展示该项目下所有学生（含未提交），便于代提交
        project = get_object_or_404(Project, pk=project_id)
        students = User.objects.filter(is_staff=False, is_active=True).order_by("student_id")
        if search:
            students = students.filter(
                Q(student_id__icontains=search) | Q(name__icontains=search)
            )

        subs = Submission.objects.filter(project=project, student__in=students)
        sub_map = {sub.student_id: sub for sub in subs}

        rows = []
        for stu in students:
            sub = sub_map.get(stu.pk)
            has_sub = sub is not None
            # 状态筛选
            if status == "submitted" and not has_sub:
                continue
            if status == "unsubmitted" and has_sub:
                continue
            # 申诉筛选（仅对已提交记录有意义）
            if appeal == "yes" and not (has_sub and sub.appeal_submitted):
                continue
            if appeal == "no" and not (has_sub and not sub.appeal_submitted):
                continue
            rows.append({"student": stu, "submission": sub})

        submitted_count = sum(1 for r in rows if r["submission"])
        unsubmitted_count = len(rows) - submitted_count

        return render(request, "admin_panel/submission_list.html", {
            "matrix_mode": True,
            "project": project,
            "rows": rows,
            "submitted_count": submitted_count,
            "unsubmitted_count": unsubmitted_count,
            "filter_projects": filter_projects,
            "selected_project": project_id,
            "search_query": search,
            "selected_appeal": appeal,
            "selected_status": status,
        })

    # 平铺模式：未选项目时展示全部提交记录
    submissions = Submission.objects.select_related("project", "student").all()

    if search:
        submissions = submissions.filter(
            Q(student__student_id__icontains=search) | Q(student__name__icontains=search))
    if appeal == "yes":
        submissions = submissions.filter(appeal_submitted=True)
    elif appeal == "no":
        submissions = submissions.filter(appeal_submitted=False)

    submissions = submissions.order_by("-submitted_at")

    return render(request, "admin_panel/submission_list.html", {
        "matrix_mode": False,
        "submissions": submissions,
        "filter_projects": filter_projects,
        "selected_project": project_id,
        "search_query": search,
        "selected_appeal": appeal,
        "selected_status": status,
    })


@staff_member_required
def submission_upload_view(request, student_pk, project_pk):
    """管理员代学生提交作业（支持为尚未提交的学生新建提交）"""
    student = get_object_or_404(User, pk=student_pk, is_staff=False)
    project = get_object_or_404(Project, pk=project_pk)
    # 查找已有提交（可能为 None，表示该学生尚未提交）
    submission = (
        Submission.objects.filter(project=project, student=student)
        .select_related("project", "student")
        .first()
    )

    if request.method == "POST":
        uploaded_file = request.FILES.get("file")

        if not uploaded_file:
            messages.error(request, "请选择要上传的文件。")
            return render(request, "admin_panel/submission_upload.html", {
                "student": student, "project": project, "submission": submission,
            })

        if uploaded_file.size > project.max_file_size * 1024 * 1024:
            messages.error(request, f"文件大小超过限制（{project.max_file_size} MB）。")
            return render(request, "admin_panel/submission_upload.html", {
                "student": student, "project": project, "submission": submission,
            })

        if project.allowed_extensions:
            _, ext = os.path.splitext(uploaded_file.name)
            allowed = [e.strip().lower() for e in project.allowed_extensions.split(",")]
            if ext.lower() not in allowed:
                messages.error(request, f"不支持的文件类型，允许的类型：{project.allowed_extensions}")
                return render(request, "admin_panel/submission_upload.html", {
                    "student": student, "project": project, "submission": submission,
                })

        auto_rename = request.POST.get("auto_rename") == "on"
        original_name = uploaded_file.name
        renamed_name = original_name

        if auto_rename and project.auto_rename:
            pattern = project.rename_pattern or "{student_id} {name}{ext}"
            renamed_name = format_rename_pattern(pattern, student, project, original_name)

        Submission.objects.update_or_create(
            project=project,
            student=student,
            defaults={
                "file": uploaded_file,
                "original_filename": original_name,
                "renamed_filename": renamed_name,
                "file_size": uploaded_file.size,
                "auto_renamed": auto_rename,
            },
        )

        messages.success(request, f"已代 {student.name}({student.student_id}) 提交「{project.name}」的作业。")
        return redirect("admin_panel:submission_list")

    return render(request, "admin_panel/submission_upload.html", {
        "student": student, "project": project, "submission": submission,
    })


@staff_member_required
def download_submission_view(request, pk):
    """下载提交文件，以重命名后的文件名提供给浏览器"""
    submission = get_object_or_404(Submission.objects.select_related("project", "student"), pk=pk)
    if not submission.file:
        messages.error(request, "文件不存在！")
        return redirect("admin_panel:submission_list")

    from django.http import FileResponse
    import mimetypes

    file_path = submission.file.path
    if not os.path.exists(file_path):
        messages.error(request, "文件已丢失！")
        return redirect("admin_panel:submission_list")

    download_name = submission.renamed_filename or submission.original_filename
    response = FileResponse(open(file_path, "rb"), as_attachment=True, filename=download_name)
    return response


@staff_member_required
def submission_batch_download_view(request):
    """批量下载所选提交文件为 ZIP"""
    if request.method != "POST":
        messages.error(request, "无效的请求方式。")
        return redirect("admin_panel:submission_list")

    ids_str = request.POST.get("ids", "")
    if not ids_str:
        messages.error(request, "请先选择要下载的提交记录。")
        return redirect("admin_panel:submission_list")

    ids = [int(x) for x in ids_str.split(",") if x.strip()]
    if not ids:
        messages.error(request, "请先选择要下载的提交记录。")
        return redirect("admin_panel:submission_list")

    submissions = Submission.objects.filter(pk__in=ids).select_related("project", "student")

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for sub in submissions:
            if sub.file and os.path.exists(sub.file.path):
                zip_name = sub.renamed_filename or sub.original_filename
                # 如果同一个文件名出现多次，添加学号前缀避免冲突
                existing_names = [info.filename for info in zf.filelist]
                if zip_name in existing_names:
                    base, ext = os.path.splitext(zip_name)
                    zip_name = f"{sub.student.student_id}_{base}{ext}"
                zf.write(sub.file.path, zip_name)

    zip_buffer.seek(0)
    response = HttpResponse(zip_buffer.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="submissions_batch_{timezone.now().strftime("%Y%m%d_%H%M%S")}.zip"'
    return response


# ==================== 用户管理（自建 CRUD） ====================

@staff_member_required
def user_list_view(request):
    users = User.objects.all().order_by("-created_at")
    return render(request, "admin_panel/user_list.html", {"users": users})


@staff_member_required
def user_create_view(request):
    if request.method == "POST":
        sid = request.POST.get("student_id", "")
        name = request.POST.get("name", "")
        password = request.POST.get("password", "")
        is_staff = request.POST.get("is_staff") == "on"
        is_active = request.POST.get("is_active", "on") == "on"

        if User.objects.filter(student_id=sid).exists():
            messages.error(request, f"学号「{sid}」已存在！")
            return render(request, "admin_panel/user_form.html", {"title": "新建用户", "form_values": {
                "student_id": sid, "name": name, "is_staff": is_staff, "is_active": is_active, "is_edit": False,
            }})

        user = User.objects.create_user(
            student_id=sid, name=name, password=password,
            is_staff=is_staff, is_active=is_active)
        messages.success(request, f"用户「{user.name}」已创建！")
        return redirect("admin_panel:user_list")

    return render(request, "admin_panel/user_form.html", {"title": "新建用户", "form_values": {
        "student_id": "",
        "name": "",
        "is_staff": False,
        "is_active": True,
        "is_edit": False,
    }})


@staff_member_required
def user_edit_view(request, pk):
    user = get_object_or_404(User, pk=pk)

    if request.method == "POST":
        user.name = request.POST.get("name", user.name)
        user.is_staff = request.POST.get("is_staff") == "on"
        user.is_active = request.POST.get("is_active", "on") == "on"
        password = request.POST.get("password", "")
        if password:
            user.set_password(password)
        user.save()
        messages.success(request, f"用户「{user.name}」已更新！")
        return redirect("admin_panel:user_list")

    return render(request, "admin_panel/user_form.html", {
        "title": f"编辑：{user.name}",
        "form_values": {
            "student_id": user.student_id,
            "name": user.name,
            "is_staff": user.is_staff,
            "is_active": user.is_active,
            "is_edit": True,
        },
    })


@staff_member_required
def user_delete_view(request, pk):
    user = get_object_or_404(User, pk=pk)
    if user.student_id == "admin":
        messages.error(request, "不能删除超级管理员！")
    else:
        name = user.name
        user.delete()
        messages.success(request, f"用户「{name}」已删除！")
    return redirect("admin_panel:user_list")


# ==================== 备份与清理 ====================

@staff_member_required
def backup_now_view(request):
    """立即执行一次备份（仅在 POST 下生效，避免误触发）"""
    if request.method != "POST":
        messages.info(request, "请通过页面按钮执行备份。")
        return redirect("admin_panel:settings")
    try:
        from django.core.management import call_command
        call_command("backup_and_cleanup")
        messages.success(request, "备份任务已执行，详见服务器日志。")
    except Exception as e:
        messages.error(request, f"备份失败：{e}")
    return redirect("admin_panel:settings")


@staff_member_required
def cleanup_now_view(request):
    """安全清理本地存档：仅删除超过保留期的提交文件，绝不删除整个目录"""
    if request.method != "POST":
        messages.info(request, "请通过页面按钮执行清理。")
        return redirect("admin_panel:settings")
    try:
        s = LiveSettings.load()
        days = s.cleanup_after_backup
        media_root = settings.MEDIA_ROOT

        if not os.path.exists(media_root):
            messages.info(request, "本地存档目录不存在，无需清理。")
            return redirect("admin_panel:settings")

        if days == 0:
            # 立即清理：删除所有提交的本地文件，但保留目录与数据库记录
            qs = Submission.objects.all()
        else:
            cutoff = timezone.now() - timedelta(days=days)
            qs = Submission.objects.filter(submitted_at__lt=cutoff)

        count = 0
        for sub in qs:
            if sub.file:
                local_path = os.path.join(media_root, sub.file.name)
                if os.path.exists(local_path):
                    try:
                        os.remove(local_path)
                        count += 1
                    except OSError:
                        pass
        messages.success(
            request,
            f"安全清理完成，共删除 {count} 个过期本地文件（目录与未过期文件已保留）。",
        )
    except Exception as e:
        messages.error(request, f"清理失败：{e}")
    return redirect("admin_panel:settings")


@staff_member_required
def check_backup_view(request):
    """检查备份相关配置状态"""
    if request.method != "POST":
        messages.info(request, "请通过页面按钮检查备份状态。")
        return redirect("admin_panel:settings")

    s = LiveSettings.load()
    if s.backup_enabled:
        messages.success(request, "自动备份已启用。")
    else:
        messages.warning(request, "自动备份未启用。")

    remote = []
    if s.webdav_url and s.webdav_username:
        remote.append("WebDAV")
    if s.s3_bucket and s.s3_access_key:
        remote.append("S3")
    if s.smb_host and s.smb_share:
        remote.append("SMB")
    if remote:
        messages.success(request, f"已配置远程存储：{', '.join(remote)}。")
    else:
        messages.warning(request, "尚未配置任何远程存储，备份将仅针对远程项目生效。")

    return redirect("admin_panel:settings")


# ==================== 连接测试 ====================

@staff_member_required
def test_connection_view(request):
    """测试远程存储后端连接（支持 AJAX 和普通请求）"""
    backend = request.POST.get("backend", "s3")
    s = LiveSettings.load()

    try:
        storage = get_storage_backend(backend, s)
        ok, msg = storage.test_connection()
    except Exception as e:
        ok, msg = False, f"初始化失败: {e}"

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"ok": ok, "message": msg})
    if ok:
        messages.success(request, msg)
    else:
        messages.error(request, msg)
    return redirect("admin_panel:settings")


# ==================== 备份历史 ====================

@staff_member_required
def backup_history_view(request):
    """显示备份历史记录"""
    records = BackupRecord.objects.all()[:50]
    return render(request, "admin_panel/backup_history.html", {"records": records})


# ==================== 从备份恢复 ====================

@staff_member_required
def restore_view(request):
    """显示可用备份列表，支持从备份恢复"""
    s = LiveSettings.load()

    # 确定后端
    backend_type = request.GET.get("backend", "")
    if not backend_type:
        if s.s3_bucket and s.s3_access_key and s.s3_secret_key:
            backend_type = "s3"
        elif s.webdav_url and s.webdav_username:
            backend_type = "webdav"
        elif s.smb_host and s.smb_share:
            backend_type = "smb"

    backups = []
    error_msg = ""

    if backend_type:
        try:
            storage = get_storage_backend(backend_type, s)
            ok, msg = storage.test_connection()
            if ok:
                all_files = storage.list_files_with_info("backups/")
                # 按 manifest.json 聚合
                backup_map = {}
                for f in all_files:
                    key = f["key"]
                    if key.endswith("manifest.json"):
                        prefix = key.rsplit("manifest.json", 1)[0]
                        backup_map[prefix] = {
                            "prefix": prefix,
                            "manifest_key": key,
                            "info": f,
                            "file_count": None,
                            "database_backed_up": None,
                            "total_size_display": None,
                        }

                # 尝试读取每个备份的 manifest
                for prefix, data in backup_map.items():
                    manifest = _fetch_remote_manifest(storage, data["manifest_key"])
                    if manifest:
                        data["file_count"] = manifest.get("file_count")
                        data["database_backed_up"] = manifest.get("database_backed_up")
                        data["errors"] = manifest.get("errors", [])

                    # 计算该备份的总大小
                    backup_files = [f for f in all_files if f["key"].startswith(prefix)]
                    total_size = sum(f.get("size", 0) for f in backup_files)
                    data["total_size_display"] = _format_size(total_size)

                    modified = data["info"].get("last_modified", "")
                    if hasattr(modified, "strftime"):
                        modified = modified.strftime("%Y-%m-%d %H:%M:%S")
                    data["modified_display"] = str(modified)

                    backups.append(data)

                backups.sort(key=lambda x: x["prefix"], reverse=True)
            else:
                error_msg = msg
        except Exception as e:
            error_msg = str(e)

    return render(request, "admin_panel/restore.html", {
        "backups": backups,
        "backend_type": backend_type,
        "error_msg": error_msg,
    })


@staff_member_required
@require_POST
def restore_execute_view(request):
    """执行从备份恢复"""
    prefix = request.POST.get("prefix", "")
    skip_db = request.POST.get("skip_db") == "on"
    s = LiveSettings.load()

    if not prefix:
        messages.error(request, "请指定要恢复的备份。")
        return redirect("admin_panel:restore")

    # 确定后端
    backend_type = ""
    if s.s3_bucket and s.s3_access_key and s.s3_secret_key:
        backend_type = "s3"
    elif s.webdav_url and s.webdav_username:
        backend_type = "webdav"
    elif s.smb_host and s.smb_share:
        backend_type = "smb"

    if not backend_type:
        messages.error(request, "没有可用的远程存储后端。")
        return redirect("admin_panel:restore")

    if not prefix.endswith("/"):
        prefix += "/"

    try:
        storage = get_storage_backend(backend_type, s)
        all_files = storage.list_files_with_info(prefix)

        if not all_files:
            messages.error(request, f"未找到备份文件: {prefix}")
            return redirect("admin_panel:restore")

        # 分类
        db_file = None
        media_files = []
        for f in all_files:
            key = f["key"]
            if key.endswith("db.sqlite3"):
                db_file = key
            elif "/media/" in key:
                media_files.append(key)

        restored_count = 0
        errors = []

        # 恢复数据库
        if not skip_db and db_file:
            import tempfile
            import shutil
            tmp_db = os.path.join(tempfile.gettempdir(), "db_restore.sqlite3")
            try:
                storage.download(db_file, tmp_db)
                from django.db import connection
                connection.close()
                db_path = str(settings.DATABASES["default"]["NAME"])
                # 备份当前数据库
                if os.path.exists(db_path):
                    backup_db = db_path + ".pre_restore"
                    shutil.copy2(db_path, backup_db)
                shutil.move(tmp_db, db_path)
                restored_count += 1
                messages.success(request, "数据库已恢复。")
            except Exception as e:
                errors.append(f"数据库恢复失败: {e}")

        # 恢复媒体文件
        media_root = str(settings.MEDIA_ROOT)
        for key in media_files:
            media_idx = key.find("/media/")
            if media_idx == -1:
                continue
            rel_path = key[media_idx + len("/media/"):]
            local_path = os.path.join(media_root, rel_path.replace("/", os.sep))
            try:
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                storage.download(key, local_path)
                restored_count += 1
            except Exception as e:
                errors.append(f"{rel_path}: {e}")

        if errors:
            messages.warning(request, f"恢复完成（{restored_count} 个成功，{len(errors)} 个失败）：\n" + "\n".join(errors[:5]))
        else:
            messages.success(request, f"恢复完成，共恢复 {restored_count} 个文件。")

        if not skip_db and db_file:
            messages.warning(request, "数据库已恢复，建议重启应用以使更改生效。")

    except Exception as e:
        messages.error(request, f"恢复失败: {e}")

    return redirect("admin_panel:restore")


# ==================== 辅助函数 ====================

def _fetch_remote_manifest(storage, manifest_key):
    """下载并解析远程 manifest.json"""
    import tempfile
    tmp_dir = tempfile.mkdtemp()
    tmp_file = os.path.join(tmp_dir, "manifest.json")
    try:
        storage.download(manifest_key, tmp_file)
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


def _format_size(size):
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"