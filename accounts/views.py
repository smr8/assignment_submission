from django.contrib import messages
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from .forms import LoginForm, RegisterForm
from .models import User


def register_view(request):
    if request.user.is_authenticated:
        return redirect("home")
    if request.method == "POST":
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            auth_login(request, user)
            messages.success(request, f"注册成功！欢迎你，{user.name}！")
            return redirect("home")
    else:
        form = RegisterForm()
    return render(request, "accounts/register.html", {"form": form})


def login_view(request):
    if request.user.is_authenticated:
        return redirect("home")
    if request.method == "POST":
        form = LoginForm(request=request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            auth_login(request, user)
            messages.success(request, f"登录成功！欢迎回来，{user.name}！")
            next_url = request.GET.get("next", "home")
            return redirect(next_url)
    else:
        form = LoginForm(request=request)
    return render(request, "accounts/login.html", {"form": form})


@login_required
def account_manage_view(request):
    if request.method == "POST":
        user = request.user
        new_name = request.POST.get("name", "").strip()
        new_student_id = request.POST.get("student_id", "").strip()
        new_password = request.POST.get("password", "")

        if not new_name or not new_student_id:
            messages.error(request, "姓名和学号不能为空。")
            return render(request, "accounts/account_manage.html")

        if new_student_id != user.student_id and User.objects.filter(student_id=new_student_id).exists():
            messages.error(request, f"学号「{new_student_id}」已被占用。")
            return render(request, "accounts/account_manage.html")

        user.name = new_name
        user.student_id = new_student_id
        if new_password:
            user.set_password(new_password)
            # 修改密码后需要重新登录
            user.save()
            auth_logout(request)
            messages.success(request, "账号信息已更新，请使用新密码重新登录。")
            return redirect("login")

        user.save()
        messages.success(request, "账号信息已更新。")
        return redirect("account_manage")

    return render(request, "accounts/account_manage.html")


def logout_view(request):
    auth_logout(request)
    messages.info(request, "您已退出登录。")
    return redirect("login")