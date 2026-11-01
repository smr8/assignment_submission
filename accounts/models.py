from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models


class UserQuerySet(models.QuerySet):
    def class_members(self, include_inactive=False):
        """班内成员 —— 提交率统计与提交名单的口径。

        管理员本身也是班上一员，因此这里不按 ``is_staff`` 过滤；
        只有显式标记为非班内成员（纯系统账号、外班/教师账号）的人才会被排除。
        停用账号默认不计入，需要核对历史记录时可传 ``include_inactive=True``。
        """
        qs = self.filter(is_class_member=True)
        return qs if include_inactive else qs.filter(is_active=True)


class UserManager(BaseUserManager.from_queryset(UserQuerySet)):
    def create_user(self, student_id, name, password=None, **extra_fields):
        if not student_id:
            raise ValueError("学号不能为空")
        if not name:
            raise ValueError("姓名不能为空")
        user = self.model(student_id=student_id, name=name, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, student_id, name, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_admin", True)
        return self.create_user(student_id, name, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    student_id = models.CharField("学号", max_length=32, unique=True, db_index=True)
    name = models.CharField("姓名", max_length=64)
    is_active = models.BooleanField("激活", default=True)
    is_staff = models.BooleanField("管理员权限", default=False)
    is_admin = models.BooleanField("超级管理员", default=False)
    is_class_member = models.BooleanField(
        "计入班级统计",
        default=True,
        help_text="班上一员即勾选；管理员同样计入。纯系统账号或外班/教师账号取消勾选",
    )
    created_at = models.DateTimeField("注册时间", auto_now_add=True)

    objects = UserManager()

    USERNAME_FIELD = "student_id"
    REQUIRED_FIELDS = ["name"]

    class Meta:
        verbose_name = "用户"
        verbose_name_plural = verbose_name
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.student_id} {self.name}"