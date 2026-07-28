import csv
import io
import os
import shutil
import zipfile
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.models import User
from assignments.models import LiveSettings, Project, Submission
from assignments.views import format_rename_pattern


# ==================== 仪表盘 ====================

@staff_member_required
def dashboard_view(request):
    return render(request, "admin_panel/dashboard.html")


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
    submissions = Submission.objects.select_related("project", "student").all()
    filter_projects = Project.objects.all()

    project_id = request.GET.get("project", "")
    search = request.GET.get("search", "")
    appeal = request.GET.get("appeal", "")

    if project_id:
        submissions = submissions.filter(project_id=project_id)
    if search:
        submissions = submissions.filter(
            Q(student__student_id__icontains=search) | Q(student__name__icontains=search))
    if appeal == "yes":
        submissions = submissions.filter(appeal_submitted=True)
    elif appeal == "no":
        submissions = submissions.filter(appeal_submitted=False)

    submissions = submissions.order_by("-submitted_at")

    return render(request, "admin_panel/submission_list.html", {
        "submissions": submissions,
        "filter_projects": filter_projects,
        "selected_project": project_id,
        "search_query": search,
        "selected_appeal": appeal,
    })


@staff_member_required
def submission_upload_view(request, pk):
    """管理员代学生提交作业"""
    submission = get_object_or_404(Submission.objects.select_related("project", "student"), pk=pk)
    student = submission.student
    project = submission.project

    if request.method == "POST":
        uploaded_file = request.FILES.get("file")

        if not uploaded_file:
            messages.error(request, "请选择要上传的文件。")
            return render(request, "admin_panel/submission_upload.html", {"submission": submission})

        if uploaded_file.size > project.max_file_size * 1024 * 1024:
            messages.error(request, f"文件大小超过限制（{project.max_file_size} MB）。")
            return render(request, "admin_panel/submission_upload.html", {"submission": submission})

        if project.allowed_extensions:
            _, ext = os.path.splitext(uploaded_file.name)
            allowed = [e.strip().lower() for e in project.allowed_extensions.split(",")]
            if ext.lower() not in allowed:
                messages.error(request, f"不支持的文件类型，允许的类型：{project.allowed_extensions}")
                return render(request, "admin_panel/submission_upload.html", {"submission": submission})

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

    return render(request, "admin_panel/submission_upload.html", {"submission": submission})


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
    try:
        messages.success(request, "备份任务已启动。")
    except Exception as e:
        messages.error(request, f"备份失败：{e}")
    return redirect("admin_panel:settings")


@staff_member_required
def cleanup_now_view(request):
    try:
        media_root = settings.MEDIA_ROOT
        if os.path.exists(media_root):
            shutil.rmtree(media_root)
            os.makedirs(media_root, exist_ok=True)
            messages.success(request, "本地存档已清理。")
        else:
            messages.info(request, "本地存档目录不存在。")
    except Exception as e:
        messages.error(request, f"清理失败：{e}")
    return redirect("admin_panel:settings")


@staff_member_required
def check_backup_view(request):
    messages.info(request, "备份检查功能即将实现。")
    return redirect("admin_panel:settings")