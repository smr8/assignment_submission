import os
import re

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .models import Project, Submission


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


@login_required
def home_view(request):
    """学生首页：展示作业项目、提交状态、统计与筛选"""
    now = timezone.now()
    user = request.user

    status_filter = request.GET.get("status", "all")
    search = request.GET.get("search", "").strip()

    projects = Project.objects.filter(is_active=True).order_by("deadline")

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
    """作业提交页面"""
    project = get_object_or_404(Project, slug=slug, is_active=True)
    user = request.user

    existing_submission = Submission.objects.filter(project=project, student=user).first()

    if request.method == "POST":
        uploaded_file = request.FILES.get("file")
        if not uploaded_file:
            messages.error(request, "请选择要上传的文件。")
            return render(request, "assignments/submit.html", {
                "project": project,
                "existing_submission": existing_submission,
            })

        # 检查文件大小
        if uploaded_file.size > project.max_file_size * 1024 * 1024:
            messages.error(request, f"文件大小超过限制（{project.max_file_size} MB）。")
            return render(request, "assignments/submit.html", {
                "project": project,
                "existing_submission": existing_submission,
            })

        # 检查文件类型
        if project.allowed_extensions:
            _, ext = os.path.splitext(uploaded_file.name)
            allowed = [e.strip().lower() for e in project.allowed_extensions.split(",")]
            if ext.lower() not in allowed:
                messages.error(request, f"不支持的文件类型，允许的类型：{project.allowed_extensions}")
                return render(request, "assignments/submit.html", {
                    "project": project,
                    "existing_submission": existing_submission,
                })

        auto_rename = request.POST.get("auto_rename") == "on"
        appeal = request.POST.get("appeal", "").strip()

        # 自动重命名
        original_name = uploaded_file.name
        renamed_name = original_name

        if auto_rename and project.auto_rename:
            pattern = project.rename_pattern or "{student_id} {name}{ext}"
            renamed_name = format_rename_pattern(pattern, user, project, original_name)

        # 保存文件
        submission, created = Submission.objects.update_or_create(
            project=project,
            student=user,
            defaults={
                "file": uploaded_file,
                "original_filename": original_name,
                "renamed_filename": renamed_name,
                "file_size": uploaded_file.size,
                "auto_renamed": auto_rename,
                "appeal": appeal,
                "appeal_submitted": bool(appeal),
            },
        )

        messages.success(request, f"作业提交成功！{'（已自动重命名）' if auto_rename else ''}")
        return redirect("home")

    return render(request, "assignments/submit.html", {
        "project": project,
        "existing_submission": existing_submission,
    })