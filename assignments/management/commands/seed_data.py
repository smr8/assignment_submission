"""
生成演示用种子数据：管理员、学生、作业项目（含一个已截止项目）与示例提交（含一条逾期补交）。
可重复执行，已存在的数据不会重复创建。
"""
from datetime import timedelta

from django.core.files.base import ContentFile
from django.utils import timezone

from django.core.management.base import BaseCommand

from accounts.models import User
from assignments.models import LiveSettings, Project, Submission


class Command(BaseCommand):
    help = "生成演示用种子数据（管理员/学生/项目/提交）"

    def handle(self, *args, **options):
        # 1. 系统设置单例
        LiveSettings.load()

        # 2. 管理员
        admin, created = User.objects.get_or_create(
            student_id="admin",
            defaults={"name": "管理员", "is_staff": True, "is_active": True},
        )
        if created:
            admin.set_password("admin123")
            admin.save()
            self.stdout.write(self.style.SUCCESS("✅ 创建管理员  admin / admin123"))

        # 3. 学生
        students_data = [
            ("2021001", "张三"),
            ("2021002", "李四"),
            ("2021003", "王五"),
            ("2021004", "赵六"),
        ]
        students = []
        for sid, name in students_data:
            u, created = User.objects.get_or_create(
                student_id=sid,
                defaults={"name": name, "is_active": True},
            )
            if created:
                u.set_password("stu12345")
                u.save()
                self.stdout.write(self.style.SUCCESS(f"✅ 创建学生  {name} ({sid}) / stu12345"))
            students.append(u)

        now = timezone.now()

        # 4. 作业项目（第三个已截止）
        projects_data = [
            ("第一次作业：Python 基础", "hw1-python", "完成 Python 基础练习题。", now + timedelta(days=2), True),
            ("第二次作业：Web 开发", "hw2-web", "使用 Django 完成一个简单的 Web 应用。", now + timedelta(days=10), True),
            ("课程论文", "paper", "提交课程期末论文。", now - timedelta(days=3), True),
        ]
        projects = []
        for name, slug, desc, deadline, active in projects_data:
            p, created = Project.objects.get_or_create(
                slug=slug,
                defaults={
                    "name": name,
                    "description": desc,
                    "deadline": deadline,
                    "storage_backend": "local",
                    "is_active": active,
                    "auto_rename": True,
                    "rename_pattern": "{student_id} {name}{ext}",
                },
            )
            projects.append(p)
            if created:
                self.stdout.write(self.style.SUCCESS(f"✅ 创建项目  {name}"))

        # 5. 示例提交（王五对课程论文逾期补交）
        def make_submission(project, student, late=False):
            if Submission.objects.filter(project=project, student=student).exists():
                return
            filename = f"{student.student_id}_{student.name}.txt"
            content = ContentFile(b"demo submission content")
            sub = Submission(
                project=project,
                student=student,
                original_filename=filename,
                renamed_filename=filename,
                file_size=len(b"demo submission content"),
                auto_renamed=True,
            )
            sub.file.save(filename, content, save=True)
            # 模拟逾期：首次保存后将被 auto_now_add 设为 now，这里覆盖为截止后时间
            if late and project.deadline:
                Submission.objects.filter(pk=sub.pk).update(
                    submitted_at=project.deadline + timedelta(hours=5)
                )

        for stu in students[:2]:
            make_submission(projects[0], stu)
            make_submission(projects[1], stu)
        make_submission(projects[2], students[2], late=True)

        self.stdout.write(self.style.SUCCESS("\n🎉 种子数据生成完成！"))
        self.stdout.write("   管理员: admin / admin123")
        self.stdout.write("   学生:   2021001~2021004 / stu12345")
