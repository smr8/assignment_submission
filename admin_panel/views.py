import csv
import io
import json
import os
import re
import zipfile
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import User
from assignments.models import (
    BackupRecord,
    LiveSettings,
    Project,
    ProjectAttachment,
    ProjectImage,
    Submission,
)
from assignments.views import (
    format_rename_pattern,
    persist_submission,
    safe_delete_file,
    validate_upload,
)
from storage_backends import get_storage_backend


# ==================== 仪表盘 ====================

@staff_member_required
def dashboard_view(request):
    now = timezone.now()

    total_projects = Project.objects.count()
    total_submissions = Submission.objects.count()
    total_students = User.objects.filter(is_staff=False, is_active=True).count()

    # 已归档项目不再参与「各项目提交率」与平均提交率，避免历史项目把当前数据拉低
    projects = Project.objects.active()
    archived_projects = Project.objects.archived().count()
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
        "archived_projects": archived_projects,
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
    projects = Project.objects.active().prefetch_related("images", "attachments")
    project_stats = []
    total_students = User.objects.filter(is_staff=False, is_active=True).count()

    for project in projects:
        submitted_count = Submission.objects.filter(project=project).count()
        percentage = round(submitted_count / total_students * 100, 1) if total_students > 0 else 0
        project_stats.append({
            "project": project,
            "submitted_count": submitted_count,
            "unsubmitted_count": max(total_students - submitted_count, 0),
            "total_students": total_students,
            "percentage": percentage,
            "is_expired": project.deadline and timezone.now() > project.deadline,
        })

    return render(request, "admin_panel/stats.html", {
        "project_stats": project_stats,
        "archived_projects": Project.objects.archived().count(),
    })


@staff_member_required
def project_stats_detail_view(request, pk):
    """单个项目的提交明细：列出已交 / 未交学生的姓名与学号"""
    project = get_object_or_404(Project, pk=pk)
    students = User.objects.filter(is_staff=False, is_active=True).order_by("student_id")
    submissions = Submission.objects.filter(project=project).select_related("student")
    sub_map = {sub.student_id: sub for sub in submissions}

    submitted = []
    unsubmitted = []
    seen = set()
    for stu in students:
        seen.add(stu.pk)
        sub = sub_map.get(stu.pk)
        if sub is not None:
            submitted.append({"student": stu, "submission": sub})
        else:
            unsubmitted.append(stu)
    # 已停用 / 非活跃但仍有提交记录的学生，避免漏统计
    for sub in submissions:
        if sub.student_id not in seen:
            submitted.append({"student": sub.student, "submission": sub})

    total = len(submitted) + len(unsubmitted)

    # 导出未交名单
    if request.GET.get("format") == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8-sig")
        filename = f"unsubmitted_{project.slug}_{timezone.localtime().strftime('%Y%m%d')}.csv"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        writer = csv.writer(response)
        writer.writerow(["学号", "姓名", "作业项目", "状态"])
        for stu in unsubmitted:
            writer.writerow([stu.student_id, stu.name, project.name, "未提交"])
        return response

    return render(request, "admin_panel/stats_detail.html", {
        "project": project,
        "submitted": submitted,
        "unsubmitted": unsubmitted,
        "submitted_count": len(submitted),
        "unsubmitted_count": len(unsubmitted),
        "total_count": total,
        "percentage": round(len(submitted) / total * 100, 1) if total else 0,
        "is_expired": project.deadline and timezone.now() > project.deadline,
    })


# ==================== 导出 CSV ====================

@staff_member_required
def export_csv_view(request):
    response = HttpResponse(content_type="text/csv; charset=utf-8-sig")
    response["Content-Disposition"] = f'attachment; filename="submissions_{timezone.localtime().strftime("%Y%m%d")}.csv"'

    writer = csv.writer(response)
    writer.writerow(["学号", "姓名", "作业项目", "原始文件名", "重命名文件名", "文件大小(B)", "自动重命名", "有申诉", "提交时间"])

    submissions = Submission.objects.select_related("project", "student").all()
    for s in submissions:
        writer.writerow([
            s.student.student_id, s.student.name, s.project.name,
            s.original_filename, s.renamed_filename, s.file_size,
            "是" if s.auto_renamed else "否",
            "是" if s.appeal_submitted else "否",
            timezone.localtime(s.submitted_at).strftime("%Y-%m-%d %H:%M:%S"),
        ])
    return response


# ==================== 项目管理（自建 CRUD） ====================

# 标识只允许 ASCII 字母、数字、中划线、下划线（Django SlugField 的默认规则）
SLUG_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
# 中文、中文标点、全角字符：这些字符会带进 URL 与文件目录，必须在入库前拦下
CJK_PATTERN = re.compile(r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]")

DEFAULT_RENAME_PATTERN = "{student_id} {name}{ext}"


def validate_project_slug(slug, instance_pk=None):
    """校验项目标识，返回错误提示（None 表示通过）。

    ``Model.save()`` 不会触发字段校验，SlugField 的 ASCII 规则只有在
    ``full_clean()`` 或 ModelForm 中才生效；项目管理是自建 CRUD，
    因此必须显式校验，否则「第一次作业」这类中文标识会直接入库。
    """
    if not slug:
        return "标识不能为空。"

    if CJK_PATTERN.search(slug):
        return (
            f"标识「{slug}」包含中文或全角字符，不能保存。"
            "标识只能使用英文字母、数字、中划线和下划线，例如 homework-01。"
        )

    if not SLUG_PATTERN.match(slug):
        return f"标识「{slug}」含有不允许的字符，只能使用英文字母、数字、中划线和下划线（不能有空格）。"

    max_length = Project._meta.get_field("slug").max_length
    if len(slug) > max_length:
        return f"标识过长，最多 {max_length} 个字符。"

    qs = Project.objects.filter(slug=slug)
    if instance_pk is not None:
        qs = qs.exclude(pk=instance_pk)
    occupied = qs.first()
    if occupied is not None:
        return f"标识「{slug}」已被项目「{occupied.name}」占用，请换一个。"

    return None


def _project_form_values(post):
    """把表单输入整理成统一的字典，校验失败时用于原样回填"""
    return {
        "name": (post.get("name") or "").strip(),
        "slug": (post.get("slug") or "").strip(),
        "description": post.get("description", ""),
        "rename_pattern": post.get("rename_pattern") or DEFAULT_RENAME_PATTERN,
        "storage_backend": post.get("storage_backend", "local"),
        "max_file_size": post.get("max_file_size") or "",
        "deadline": post.get("deadline", ""),
        "allowed_extensions": post.get("allowed_extensions", ""),
        "is_active": post.get("is_active") == "on",
        "auto_rename": post.get("auto_rename") == "on",
    }


def _validate_project_form(values, instance_pk=None):
    """校验项目表单，返回 (错误列表, 标识错误, 文件大小, 截止时间)"""
    errors = []
    if not values["name"]:
        errors.append("项目名称不能为空。")

    slug_error = validate_project_slug(values["slug"], instance_pk=instance_pk)
    if slug_error:
        errors.append(slug_error)

    try:
        max_file_size = int(values["max_file_size"])
        if max_file_size < 1:
            raise ValueError
    except (TypeError, ValueError):
        max_file_size = 0
        errors.append("最大文件大小必须是大于 0 的整数（单位 MB）。")

    deadline = None
    if values["deadline"]:
        try:
            deadline = timezone.datetime.fromisoformat(values["deadline"])
        except ValueError:
            errors.append("截止时间格式无法识别，请重新选择。")

    return errors, slug_error, max_file_size, deadline


def _render_project_form(request, title, form_values, errors=(), slug_error="",
                         images=None, attachments=None):
    for err in errors:
        messages.error(request, err)
    return render(request, "admin_panel/project_form.html", {
        "title": title,
        "form_values": form_values,
        "slug_error": slug_error,
        "images": images or [],
        "attachments": attachments or [],
        "image_extensions": ",".join(settings.PROJECT_IMAGE_EXTENSIONS),
    })


def _project_list_url(scope):
    url = reverse("admin_panel:project_list")
    if scope in ("active", "archived", "all"):
        return f"{url}?scope={scope}"
    return url


@staff_member_required
def project_list_view(request):
    scope = request.GET.get("scope", "active")
    if scope not in ("active", "archived", "all"):
        scope = "active"

    qs = Project.objects.all()
    if scope == "active":
        qs = qs.active()
    elif scope == "archived":
        qs = qs.archived()

    projects = qs.prefetch_related("images", "attachments").order_by("-created_at")

    expired_active_count = Project.objects.active().filter(
        deadline__isnull=False, deadline__lt=timezone.now()
    ).count()

    return render(request, "admin_panel/project_list.html", {
        "projects": projects,
        "scope": scope,
        "counts": {
            "active": Project.objects.active().count(),
            "archived": Project.objects.archived().count(),
            "all": Project.objects.count(),
        },
        "expired_active_count": expired_active_count,
    })


@staff_member_required
def project_create_view(request):
    title = "新建作业项目"

    if request.method == "POST":
        values = _project_form_values(request.POST)
        errors, slug_error, max_file_size, deadline = _validate_project_form(values)
        if errors:
            # 校验不通过：不建项目，原样回填输入，避免管理员重打一遍
            return _render_project_form(request, title, values, errors, slug_error)

        project = Project(
            name=values["name"],
            slug=values["slug"],
            description=values["description"],
            is_active=values["is_active"],
            auto_rename=values["auto_rename"],
            rename_pattern=values["rename_pattern"],
            storage_backend=values["storage_backend"],
            max_file_size=max_file_size,
            allowed_extensions=values["allowed_extensions"],
            deadline=deadline,
        )
        project.save()

        image_count, attachment_count, errs = _save_project_assets(project, request)
        msg = f"项目「{project.name}」已创建！"
        if image_count or attachment_count:
            msg += f"（新增 {image_count} 张图片、{attachment_count} 个附件）"
        messages.success(request, msg)
        for err in errs:
            messages.warning(request, err)
        if request.POST.get("_save_and_stay") == "1":
            return redirect("admin_panel:project_edit", pk=project.pk)
        return redirect("admin_panel:project_list")

    return render(request, "admin_panel/project_form.html", {
        "title": title,
        "form_values": {
            "name": "",
            "slug": "",
            "description": "",
            "rename_pattern": DEFAULT_RENAME_PATTERN,
            "storage_backend": "local",
            "max_file_size": 50,
            "deadline": "",
            "allowed_extensions": "",
            "is_active": True,
            "auto_rename": True,
        },
        "slug_error": "",
        "images": [],
        "attachments": [],
        "image_extensions": ",".join(settings.PROJECT_IMAGE_EXTENSIONS),
    })


@staff_member_required
def project_edit_view(request, pk):
    project = get_object_or_404(Project, pk=pk)
    title = f"编辑：{project.name}"

    if request.method == "POST":
        values = _project_form_values(request.POST)
        errors, slug_error, max_file_size, deadline = _validate_project_form(
            values, instance_pk=project.pk
        )
        if errors:
            return _render_project_form(
                request, title, values, errors, slug_error,
                images=project.images.all(), attachments=project.attachments.all(),
            )

        project.name = values["name"]
        project.slug = values["slug"]
        project.description = values["description"]
        project.is_active = values["is_active"]
        project.auto_rename = values["auto_rename"]
        project.rename_pattern = values["rename_pattern"]
        project.storage_backend = values["storage_backend"]
        project.max_file_size = max_file_size
        project.allowed_extensions = values["allowed_extensions"]
        project.deadline = deadline
        project.save()

        _update_project_assets(project, request)
        image_count, attachment_count, errs = _save_project_assets(project, request)
        msg = f"项目「{project.name}」已更新！"
        if image_count or attachment_count:
            msg += f"（新增 {image_count} 张图片、{attachment_count} 个附件）"
        messages.success(request, msg)
        for err in errs:
            messages.warning(request, err)
        return redirect("admin_panel:project_edit", pk=project.pk)

    return render(request, "admin_panel/project_form.html", {
        "title": title,
        "form_values": {
            "name": project.name,
            "slug": project.slug,
            "description": project.description or "",
            "rename_pattern": project.rename_pattern,
            "storage_backend": project.storage_backend,
            "max_file_size": project.max_file_size,
            "deadline": timezone.localtime(project.deadline).strftime("%Y-%m-%dT%H:%M") if project.deadline else "",
            "allowed_extensions": project.allowed_extensions or "",
            "is_active": project.is_active,
            "auto_rename": project.auto_rename,
        },
        "slug_error": "",
        "images": project.images.all(),
        "attachments": project.attachments.all(),
        "image_extensions": ",".join(settings.PROJECT_IMAGE_EXTENSIONS),
    })


@staff_member_required
@require_POST
def project_archive_view(request, pk):
    """归档项目：学生端隐藏、停止接收提交，提交记录与文件全部保留"""
    project = get_object_or_404(Project, pk=pk)
    if project.archived_at:
        messages.info(request, f"项目「{project.name}」已经是归档状态。")
    else:
        project.archived_at = timezone.now()
        project.save(update_fields=["archived_at", "updated_at"])
        messages.success(
            request,
            f"项目「{project.name}」已归档：学生端不再显示，提交记录与文件保留，可随时恢复。",
        )
    return redirect(_project_list_url(request.POST.get("scope")))


@staff_member_required
@require_POST
def project_unarchive_view(request, pk):
    """恢复归档项目"""
    project = get_object_or_404(Project, pk=pk)
    if not project.archived_at:
        messages.info(request, f"项目「{project.name}」未处于归档状态。")
    else:
        project.archived_at = None
        project.save(update_fields=["archived_at", "updated_at"])
        messages.success(request, f"项目「{project.name}」已恢复，学生端重新可见。")
    return redirect(_project_list_url(request.POST.get("scope")))


@staff_member_required
@require_POST
def project_archive_finished_view(request):
    """批量归档：只处理已过截止时间且尚未归档的项目"""
    now = timezone.now()
    qs = Project.objects.active().filter(deadline__isnull=False, deadline__lt=now)
    count = qs.count()
    if count:
        qs.update(archived_at=now, updated_at=now)
        messages.success(request, f"已归档 {count} 个截止时间已过的项目。")
        return redirect(_project_list_url("archived"))
    messages.info(request, "没有需要归档的项目（只归档已过截止时间且未归档的项目）。")
    return redirect(_project_list_url("active"))



def _save_project_assets(project, request):
    """保存新建的图片（含描述）与附件，返回 (图片数, 附件数, 错误列表)"""
    errors = []
    image_count = 0
    attachment_count = 0

    image_files = request.FILES.getlist("image_files")
    captions = request.POST.getlist("image_captions")
    sort_base = project.images.count()
    allowed_image_ext = [e.lower() for e in settings.PROJECT_IMAGE_EXTENSIONS]

    for idx, uploaded in enumerate(image_files):
        _, ext = os.path.splitext(uploaded.name)
        if allowed_image_ext and ext.lower() not in allowed_image_ext:
            errors.append(f"「{uploaded.name}」不是支持的图片格式，已跳过。")
            continue
        caption = captions[idx].strip() if idx < len(captions) else ""
        image = ProjectImage(
            project=project,
            caption=caption or os.path.basename(uploaded.name),
            sort_order=sort_base + idx,
        )
        image.image = uploaded
        image.save()
        image_count += 1

    for uploaded in request.FILES.getlist("attachment_files"):
        attachment = ProjectAttachment(project=project)
        attachment.file = uploaded
        attachment.name = os.path.basename(uploaded.name)
        attachment.file_size = uploaded.size
        attachment.save()
        attachment_count += 1

    return image_count, attachment_count, errors


def _update_project_assets(project, request):
    """就地更新已有图片的描述 / 删除勾选项"""
    delete_images = set(request.POST.getlist("image_delete"))
    for image in project.images.all():
        if str(image.pk) in delete_images:
            safe_delete_file(image.image.storage, image.image.name)
            image.delete()
            continue
        new_caption = request.POST.get(f"image_caption_{image.pk}")
        if new_caption is not None and new_caption.strip() != image.caption:
            image.caption = new_caption.strip()
            image.save(update_fields=["caption"])

    delete_attachments = set(request.POST.getlist("attachment_delete"))
    for attachment in project.attachments.all():
        if str(attachment.pk) in delete_attachments:
            safe_delete_file(attachment.file.storage, attachment.file.name)
            attachment.delete()


@staff_member_required
def project_delete_view(request, pk):
    project = get_object_or_404(Project, pk=pk)
    name = project.name
    # 顺手清理磁盘上的图片 / 附件文件，避免 media 目录残留
    for image in project.images.all():
        safe_delete_file(image.image.storage, image.image.name)
    for attachment in project.attachments.all():
        safe_delete_file(attachment.file.storage, attachment.file.name)
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

    def _ctx():
        return {
            "student": student,
            "project": project,
            "submission": submission,
            "chunk_size": settings.CHUNK_UPLOAD_SIZE,
            "max_file_size_bytes": project.max_file_size * 1024 * 1024,
            "auto_rename_default": project.auto_rename,
        }

    if request.method == "POST":
        uploaded_file = request.FILES.get("file")

        if not uploaded_file:
            messages.error(request, "请选择要上传的文件。")
            return render(request, "admin_panel/submission_upload.html", _ctx())

        error = validate_upload(project, uploaded_file.name, uploaded_file.size)
        if error:
            messages.error(request, error)
            return render(request, "admin_panel/submission_upload.html", _ctx())

        auto_rename = request.POST.get("auto_rename") == "on"
        persist_submission(
            project, student, uploaded_file, uploaded_file.name,
            uploaded_file.size, auto_rename, appeal="",
        )

        messages.success(request, f"已代 {student.name}({student.student_id}) 提交「{project.name}」的作业。")
        return redirect("admin_panel:submission_list")

    return render(request, "admin_panel/submission_upload.html", _ctx())


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
    response["Content-Disposition"] = f'attachment; filename="submissions_batch_{timezone.localtime().strftime("%Y%m%d_%H%M%S")}.zip"'
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

    before_id = BackupRecord.objects.order_by("-id").values_list("id", flat=True).first()
    output = io.StringIO()
    try:
        from django.core.management import call_command
        call_command("backup_and_cleanup", stdout=output)
    except Exception as e:
        messages.error(request, f"备份执行出错：{e}")
        return redirect("admin_panel:backup_history")

    record = BackupRecord.objects.order_by("-id").first()
    tail = output.getvalue().strip().splitlines()

    if not record or record.id == before_id:
        # 命令没有落库（理论上不应发生），把原始输出回显出来便于排查
        detail = "；".join(tail[-3:]) if tail else "无输出"
        messages.error(request, f"备份未执行：{detail}")
    elif record.status == "success":
        messages.success(
            request,
            f"备份成功：{record.file_count} 个文件 / {record.total_size_display}，"
            f"耗时 {record.duration_seconds:.1f}s，路径 {record.backup_prefix}",
        )
    elif record.status == "partial":
        messages.warning(
            request,
            f"备份部分成功：{record.file_count} 个文件成功，{len(record.error_message.splitlines())} 项失败，"
            f"详情见备份历史。",
        )
    else:
        first_error = record.error_message.splitlines()[0] if record.error_message else "未知原因"
        messages.error(request, f"备份失败：{first_error}")

    return redirect("admin_panel:backup_history")


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
    status = request.GET.get("status", "")
    records = BackupRecord.objects.all()
    if status in ("success", "partial", "failed"):
        records = records.filter(status=status)

    paginator = Paginator(records, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(request, "admin_panel/backup_history.html", {
        "page_obj": page_obj,
        "records": page_obj.object_list,
        "total_count": paginator.count,
        "selected_status": status,
    })


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
                        modified = timezone.localtime(modified).strftime("%Y-%m-%d %H:%M:%S")
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