from django.db import migrations, models


def mark_system_account(apps, schema_editor):
    """把纯系统账号排除在班级统计之外。

    新增的 ``is_class_member`` 默认 True —— 管理员也是班上一员，本来就该计入。
    但 ``seed_data`` 里那个学号为 ``admin`` 的演示账号只是个登录入口，
    并不对应班里的某个人，这里顺手把它标成非班内成员，
    避免它混进「应交人数」把提交率拉低。
    """
    User = apps.get_model("accounts", "User")
    User.objects.filter(student_id="admin").update(is_class_member=False)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="is_class_member",
            field=models.BooleanField(
                default=True,
                help_text="班上一员即勾选；管理员同样计入。纯系统账号或外班/教师账号取消勾选",
                verbose_name="计入班级统计",
            ),
        ),
        migrations.RunPython(mark_system_account, migrations.RunPython.noop),
    ]
