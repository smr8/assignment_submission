import os
import re
import shutil

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files import File
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import User

from .models import Project, ProjectAttachment, ProjectImage, Submission


def format_rename_pattern(pattern, student, project, original_filename):
    """根据模板生成重命名后的文件名，确保扩展名保留"""
    name, ext = os.path.splitext(original_filename)
    timestamp = timezone.localtime().strftime("%Y%m%d_%H%M%S")
    result = pattern.format(
        student_id=student.student_id,
        name=student.name,
        project=project.name,
        timestamp=timestamp,
        original_name=name,
        ext=ext,
    )
    # 如果模板中没有 {ext} 变量，自动追回扩展名
    if "{ext}" not in pattern and ext and not result.endswith(ext):
        result += ext
    return result


# ==================== 分片上传 ====================

def _chunk_root():
    return os.path.join(str(settings.MEDIA_ROOT), settings.CHUNK_UPLOAD_TMP_DIR)


def _chunk_dir(upload_id):
    """把 upload_id 规范化后映射到临时目录，避免路径穿越"""
    safe = re.sub(r"[^0-9a-zA-Z_-]", "", str(upload_id))[:64]
    if not safe:
        raise ValueError("upload_id 无效")
    return os.path.join(_chunk_root(), safe)


def safe_delete_file(storage, name):
    """尽力删除一个已存在的文件。

    只用于「清理旧文件 / 临时文件」这类收尾动作：此时主流程已经成功，
    清理失败绝不能让请求变成错误，所以这里吞掉包括 BaseException 在内的一切异常。
    """
    if not name:
        return
    try:
        storage.delete(name)
    except BaseException:
        pass


def remove_chunk_dir(path):
    """尽最大努力清理分片目录。

    这是收尾动作：此刻上传结果已经落库并即将返回成功响应，因此清理过程中的
    任何异常都不能影响响应结果。这里刻意捕获 BaseException（包括底层库或
    运行环境抛出的 SystemExit 之类非 Exception 异常），并逐个删文件、最后删目录。
    """
    if not os.path.isdir(path):
        return
    try:
        for name in os.listdir(path):
            try:
                os.remove(os.path.join(path, name))
            except BaseException:
                pass
        os.rmdir(path)
    except BaseException:
        pass


def _purge_stale_chunks(max_age_seconds=24 * 3600):
    """清理超过 24 小时未完成的分片目录"""
    root = _chunk_root()
    if not os.path.isdir(root):
        return
    now = timezone.now().timestamp()
    for name in os.listdir(root):
        path = os.path.join(root, name)
        if not os.path.isdir(path):
            continue
        try:
            if now - os.path.getmtime(path) > max_age_seconds:
                remove_chunk_dir(path)
        except OSError:
            pass


def validate_upload(project, filename, size):
    """校验文件类型与大小，返回错误信息（None 表示通过）"""
    if project.allowed_extensions:
        _, ext = os.path.splitext(filename)
        allowed = [e.strip().lower() for e in project.allowed_extensions.split(",") if e.strip()]
        if ext.lower() not in allowed:
            return f"不支持的文件类型，允许的类型：{project.allowed_extensions}"
    if size > project.max_file_size * 1024 * 1024:
        return f"文件大小超过限制（{project.max_file_size} MB）"
    return None


def persist_submission(project, student, file_obj, original_name, size, auto_rename, appeal):
    """把已落盘的作业文件写入 Submission（存在则覆盖）"""
    renamed = original_name
    if auto_rename and project.auto_rename:
        pattern = project.rename_pattern or "{student_id} {name}{ext}"
        renamed = format_rename_pattern(pattern, student, project, original_name)

    submission = Submission.objects.filter(project=project, student=student).first()
    if submission is None:
        submission = Submission(project=project, student=student)

    old_name = submission.file.name if submission.file else ""

    submission.file = File(file_obj, name=original_name)
    submission.original_filename = original_name
    submission.renamed_filename = renamed
    submission.file_size = size
    submission.auto_renamed = auto_rename
    submission.appeal = appeal
    submission.appeal_submitted = bool(appeal)
    submission.save()

    # 覆盖提交时删除旧文件，避免 media 目录里留下孤儿文件
    if old_name and old_name != submission.file.name:
        safe_delete_file(submission.file.storage, old_name)
    return submission


@login_required
@require_POST
def chunk_upload_view(request):
    """
    分片上传端点：客户端把文件切成若干片逐一 POST 到这里，
    最后一片触发合并、校验并写入提交记录，因此单次请求体始终很小。
    """
    upload_id = request.POST.get("upload_id", "")
    filename = (request.POST.get("filename") or "").strip()
    target = request.POST.get("project", "")
    mode = request.POST.get("mode", "student")
    student_pk = request.POST.get("student_pk", "")
    auto_rename = request.POST.get("auto_rename") in ("1", "on", "true")
    appeal = request.POST.get("appeal", "")

    try:
        chunk_index = int(request.POST.get("chunk_index", "-1"))
        total_chunks = int(request.POST.get("total_chunks", "0"))
        declared_size = int(request.POST.get("file_size") or 0)
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "分片参数无效"}, status=400)

    if not upload_id or not filename or total_chunks <= 0 or not (0 <= chunk_index < total_chunks):
        return JsonResponse({"ok": False, "error": "分片参数不完整"}, status=400)

    # 解析目标项目 / 学生
    if mode == "admin":
        if not request.user.is_staff:
            return JsonResponse({"ok": False, "error": "没有权限"}, status=403)
        project = get_object_or_404(Project, pk=target)
        student = get_object_or_404(User, pk=student_pk, is_staff=False)
        success_url = reverse("admin_panel:submission_list")
    else:
        project = get_object_or_404(Project, slug=target, is_active=True, archived_at__isnull=True)
        student = request.user
        success_url = reverse("home")

    error = validate_upload(project, filename, declared_size)
    if error:
        return JsonResponse({"ok": False, "error": error}, status=400)

    chunk = request.FILES.get("chunk")
    if chunk is None:
        return JsonResponse({"ok": False, "error": "缺少分片数据"}, status=400)

    try:
        cdir = _chunk_dir(upload_id)
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)

    if not os.path.isdir(cdir):
        os.makedirs(cdir, exist_ok=True)
        _purge_stale_chunks()

    part_path = os.path.join(cdir, f"{chunk_index:08d}.part")
    try:
        with open(part_path, "wb") as dst:
            for piece in chunk.chunks():
                dst.write(piece)
    except OSError as exc:
        return JsonResponse({"ok": False, "error": f"分片写入失败：{exc}"}, status=500)

    if chunk_index != total_chunks - 1:
        return JsonResponse({
            "ok": True, "done": False,
            "received": chunk_index + 1, "total": total_chunks,
        })

    # ------- 末片：合并并落库 -------
    try:
        parts = [os.path.join(cdir, f"{i:08d}.part") for i in range(total_chunks)]
        missing = [i for i, p in enumerate(parts) if not os.path.exists(p)]
        if missing:
            return JsonResponse(
                {"ok": False, "error": f"缺少分片：{missing[:5]}"}, status=400
            )

        merged_path = os.path.join(cdir, "__merged__")
        total = 0
        with open(merged_path, "wb") as out:
            for part in parts:
                with open(part, "rb") as src:
                    shutil.copyfileobj(src, out, 1024 * 1024)
                total += os.path.getsize(part)

        error = validate_upload(project, filename, total)
        if error:
            return JsonResponse({"ok": False, "error": error}, status=400)

        with open(merged_path, "rb") as merged:
            persist_submission(project, student, merged, filename, total, auto_rename, appeal)
    except OSError as exc:
        return JsonResponse({"ok": False, "error": f"合并失败：{exc}"}, status=500)
    finally:
        remove_chunk_dir(cdir)

    return JsonResponse({
        "ok": True, "done": True,
        "redirect": success_url,
        "name": student.name,
        "student_id": student.student_id,
    })


@login_required
def download_attachment_view(request, pk):
    """下载作业附件，使用原始显示名称"""
    attachment = get_object_or_404(
        ProjectAttachment.objects.select_related("project"), pk=pk
    )
    if not attachment.file:
        raise Http404("附件不存在")
    try:
        path = attachment.file.path
    except (NotImplementedError, ValueError):
        raise Http404("附件不可用")
    if not os.path.exists(path):
        raise Http404("附件文件已丢失")
    return FileResponse(open(path, "rb"), as_attachment=True, filename=attachment.display_name)


# ==================== 页面 ====================

@login_required
def home_view(request):
    """学生首页：展示作业项目、提交状态、统计与筛选"""
    now = timezone.now()
    user = request.user

    status_filter = request.GET.get("status", "all")
    search = request.GET.get("search", "").strip()

    projects = (
        Project.objects.active()
        .filter(is_active=True)
        .prefetch_related("images", "attachments")
        .order_by("deadline")
    )

    total = projects.count()
    submitted_count = 0
    overdue_count = 0   # 已截止但仍未提交
    late_count = 0      # 逾期补交（已提交但晚于截止时间）

    project_list = []
    for project in projects:
        submission = Submission.objects.filter(project=project, student=user).first()
        is_submitted = submission is not None
        is_late = bool(submission and submission.is_late)
        is_expired = bool(project.deadline and now > project.deadline)
        due_soon = bool(
            project.deadline
            and now <= project.deadline
            and (project.deadline - now).days <= 3
        )

        if is_submitted:
            submitted_count += 1
            if is_late:
                late_count += 1
        if is_expired and not is_submitted:
            overdue_count += 1

        # 状态筛选
        if status_filter == "submitted" and not is_submitted:
            continue
        if status_filter == "unsubmitted" and is_submitted:
            continue
        if status_filter == "expired" and not (is_expired and not is_submitted):
            continue
        if status_filter == "late" and not (is_submitted and is_late):
            continue
        if search and search.lower() not in project.name.lower():
            continue

        project_list.append({
            "project": project,
            "submission": submission,
            "submitted": is_submitted,
            "is_late": is_late,
            "is_expired": is_expired,
            "due_soon": due_soon,
        })

    stats = {
        "total": total,
        "submitted": submitted_count,
        "unsubmitted": total - submitted_count,
        "overdue": overdue_count,
        "late": late_count,
    }

    return render(request, "assignments/home.html", {
        "projects": project_list,
        "stats": stats,
        "status_filter": status_filter,
        "search_query": search,
    })


@login_required
def submit_project_view(request, slug):
    """作业提交页面（未启用 JS 时退化为一次性表单上传）"""
    project = get_object_or_404(
        Project, slug=slug, is_active=True, archived_at__isnull=True
    )
    user = request.user

    existing_submission = Submission.objects.filter(project=project, student=user).first()

    if request.method == "POST":
        uploaded_file = request.FILES.get("file")
        if not uploaded_file:
            messages.error(request, "请选择要上传的文件。")
            return render(request, "assignments/submit.html", _submit_context(request, project, existing_submission))

        error = validate_upload(project, uploaded_file.name, uploaded_file.size)
        if error:
            messages.error(request, error)
            return render(request, "assignments/submit.html", _submit_context(request, project, existing_submission))

        auto_rename = request.POST.get("auto_rename") == "on"
        appeal = request.POST.get("appeal", "").strip()

        persist_submission(
            project, user, uploaded_file, uploaded_file.name,
            uploaded_file.size, auto_rename, appeal,
        )

        messages.success(request, f"作业提交成功！{'（已自动重命名）' if auto_rename else ''}")
        return redirect("home")

    return render(request, "assignments/submit.html", _submit_context(request, project, existing_submission))


def _submit_context(request, project, existing_submission):
    """提交页上下文：附带分片参数、图片、附件等"""
    return {
        "project": project,
        "existing_submission": existing_submission,
        "chunk_size": settings.CHUNK_UPLOAD_SIZE,
        "max_file_size_bytes": project.max_file_size * 1024 * 1024,
        "project_images": project.images.all(),
        "project_attachments": project.attachments.all(),
        "auto_rename_default": project.auto_rename,
    }
