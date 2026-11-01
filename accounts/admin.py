from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _

from .models import User


class UserAdmin(BaseUserAdmin):
    fieldsets = (
        (None, {"fields": ("student_id", "password")}),
        (_("个人信息"), {"fields": ("name",)}),
        (
            _("权限"),
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_admin",
                    "is_superuser",
                    "is_class_member",
                    "groups",
                    "user_permissions",
                ),
            },
        ),
        (_("时间"), {"fields": ("created_at",)}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("student_id", "name", "password1", "password2"),
            },
        ),
    )
    list_display = ("student_id", "name", "is_staff", "is_class_member", "is_active")
    list_filter = ("is_staff", "is_class_member", "is_active")
    search_fields = ("student_id", "name")
    ordering = ("-created_at",)
    readonly_fields = ("created_at",)


admin.site.register(User, UserAdmin)