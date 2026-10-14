"""回归测试：分片上传、项目资料（图片/附件）、统计明细、备份记录"""
import json
import os
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User

from .models import BackupRecord, Project, ProjectAttachment, ProjectImage, Submission

TEMP_MEDIA = tempfile.mkdtemp(prefix="assignment_test_media_")


@override_settings(MEDIA_ROOT=TEMP_MEDIA, DATA_UPLOAD_MAX_MEMORY_SIZE=1024 * 1024)
class FeatureTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)

    def setUp(self):
        self.student = User.objects.create_user(
            student_id="2024001", name="张三", password="pass12345"
        )
        self.staff = User.objects.create_user(
            student_id="admin", name="管理员", password="pass12345", is_staff=True
        )
        self.project = Project.objects.create(
            name="第一次作业", slug="hw1", max_file_size=50,
            auto_rename=True, rename_pattern="{student_id}-{name}{ext}",
        )

    # ---------- 页面渲染 ----------

    def test_admin_pages_render(self):
        self.client.force_login(self.staff)
        urls = [
            reverse("admin_panel:dashboard"),
            reverse("admin_panel:stats"),
            reverse("admin_panel:project_stats_detail", args=[self.project.pk]),
            reverse("admin_panel:backup_history"),
            reverse("admin_panel:project_list"),
            reverse("admin_panel:project_create"),
            reverse("admin_panel:project_edit", args=[self.project.pk]),
            reverse("admin_panel:submission_list"),
            reverse("admin_panel:submission_upload_for", args=[self.student.pk, self.project.pk]),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_student_pages_render(self):
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(reverse("home")).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("submit_project", args=[self.project.slug])).status_code, 200
        )

    # ---------- 分片上传 ----------

    def _upload_in_chunks(self, payload, chunk_size, filename="big.bin", mode="student",
                          project=None, student_pk=None, appeal=""):
        """按分片协议逐块上传，返回最后一次响应"""
        upload_id = "testupload"
        total = (len(payload) + chunk_size - 1) // chunk_size
        last = None
        for index in range(total):
            blob = payload[index * chunk_size:(index + 1) * chunk_size]
            data = {
                "upload_id": upload_id,
                "chunk_index": str(index),
                "total_chunks": str(total),
                "filename": filename,
                "file_size": str(len(payload)),
                "project": project if project is not None else self.project.slug,
                "mode": mode,
                "auto_rename": "1",
                "appeal": appeal,
                "chunk": SimpleUploadedFile("chunk", blob),
            }
            if student_pk is not None:
                data["student_pk"] = str(student_pk)
            last = self.client.post(reverse("chunk_upload"), data)
        return last

    def test_chunked_upload_builds_submission(self):
        """6MB 文件切成多个分片上传，单次请求体上限 1MB，必须成功"""
        self.client.force_login(self.student)
        payload = os.urandom(6 * 1024 * 1024)
        response = self._upload_in_chunks(payload, chunk_size=1024 * 1024, filename="作业.zip")
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.content)
        self.assertTrue(body["ok"], body)
        self.assertTrue(body["done"])

        submission = Submission.objects.get(project=self.project, student=self.student)
        self.assertEqual(submission.file_size, len(payload))
        self.assertEqual(submission.original_filename, "作业.zip")
        self.assertEqual(submission.renamed_filename, "2024001-张三.zip")
        with open(submission.file.path, "rb") as fh:
            self.assertEqual(fh.read(), payload)

    def test_raw_body_over_limit_is_rejected_but_chunks_pass(self):
        """对照：原始请求体仍然受上限保护；分片上传不受影响。

        说明：Django 的 DATA_UPLOAD_MAX_MEMORY_SIZE 只校验 request.body，
        multipart 文件上传不走这条路径，所以真正卡住大文件的是这一层原始体校验
        （以及 nginx 之类反向代理的 client_max_body_size）。
        """
        self.client.force_login(self.student)
        # 2MB 的表单编码请求体 > 1MB 限制 → 被拒
        response = self.client.post(
            reverse("submit_project", args=[self.project.slug]),
            data={"junk": "x" * (2 * 1024 * 1024)},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Submission.objects.exists())

    def test_chunked_upload_rejects_oversize(self):
        self.client.force_login(self.student)
        self.project.max_file_size = 1
        self.project.save()
        response = self._upload_in_chunks(b"\0" * (2 * 1024 * 1024), chunk_size=512 * 1024)
        body = json.loads(response.content)
        self.assertFalse(body["ok"])
        self.assertIn("文件大小超过限制", body["error"])
        self.assertFalse(Submission.objects.exists())

    def test_chunked_upload_admin_mode_requires_staff(self):
        self.client.force_login(self.student)
        response = self._upload_in_chunks(
            b"data", chunk_size=1024, mode="admin",
            project=str(self.project.pk), student_pk=self.student.pk,
        )
        self.assertEqual(response.status_code, 403)

    # ---------- 项目资料：图片 + 附件 ----------

    def test_project_assets_upload_and_download(self):
        self.client.force_login(self.staff)
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
        response = self.client.post(reverse("admin_panel:project_create"), {
            "name": "带资料的作业",
            "slug": "hw-assets",
            "description": "说明文字",
            "rename_pattern": "{student_id} {name}{ext}",
            "storage_backend": "local",
            "max_file_size": "50",
            "is_active": "on",
            "auto_rename": "on",
            "image_files": [SimpleUploadedFile("示意图.png", png, content_type="image/png")],
            "image_captions": ["这是作业的示意图"],
            "attachment_files": [SimpleUploadedFile("模板.docx", b"PK\x03\x04mock")],
        })
        self.assertEqual(response.status_code, 302)

        project = Project.objects.get(slug="hw-assets")
        image = ProjectImage.objects.get(project=project)
        self.assertEqual(image.caption, "这是作业的示意图")
        self.assertTrue(os.path.exists(image.image.path))

        attachment = ProjectAttachment.objects.get(project=project)
        self.assertEqual(attachment.display_name, "模板.docx")
        self.assertEqual(attachment.file_size, len(b"PK\x03\x04mock"))

        # 编辑页要能渲染「已有图片 / 已有附件」分支
        edit_page = self.client.get(reverse("admin_panel:project_edit", args=[project.pk]))
        self.assertEqual(edit_page.status_code, 200)
        self.assertContains(edit_page, "删除这张图片")
        self.assertContains(edit_page, "模板.docx")

        # 列表页显示资料数量
        list_page = self.client.get(reverse("admin_panel:project_list"))
        self.assertContains(list_page, "带资料的作业")

        # 学生端能看到图片与附件
        self.client.force_login(self.student)
        page = self.client.get(reverse("submit_project", args=["hw-assets"]))
        self.assertContains(page, "这是作业的示意图")
        self.assertContains(page, "模板.docx")

        # 附件可下载
        download = self.client.get(reverse("download_attachment", args=[attachment.pk]))
        self.assertEqual(download.status_code, 200)
        self.assertEqual(b"".join(download.streaming_content), b"PK\x03\x04mock")

    def test_project_assets_edit_caption_and_delete(self):
        self.client.force_login(self.staff)
        project = Project.objects.create(name="编辑测试", slug="hw-edit")
        image = ProjectImage.objects.create(
            project=project, caption="旧描述",
            image=SimpleUploadedFile("a.png", b"png"),
        )
        attachment = ProjectAttachment.objects.create(
            project=project, name="b.txt", file=SimpleUploadedFile("b.txt", b"hello"),
        )
        self.client.post(reverse("admin_panel:project_edit", args=[project.pk]), {
            "name": project.name,
            "slug": project.slug,
            "rename_pattern": project.rename_pattern,
            "storage_backend": "local",
            "max_file_size": "50",
            f"image_caption_{image.pk}": "新描述",
            "attachment_delete": str(attachment.pk),
        })
        image.refresh_from_db()
        self.assertEqual(image.caption, "新描述")
        self.assertFalse(ProjectAttachment.objects.filter(pk=attachment.pk).exists())

    # ---------- 统计明细 ----------

    def test_stats_detail_lists_unsubmitted_students(self):
        self.client.force_login(self.staff)
        page = self.client.get(reverse("admin_panel:project_stats_detail", args=[self.project.pk]))
        self.assertContains(page, "2024001")
        self.assertContains(page, "张三")

        Submission.objects.create(
            project=self.project, student=self.student,
            file=SimpleUploadedFile("x.txt", b"x"),
            original_filename="x.txt", file_size=1,
        )
        page = self.client.get(reverse("admin_panel:project_stats_detail", args=[self.project.pk]))
        self.assertContains(page, "全部学生均已提交")

    def test_stats_detail_csv_export(self):
        self.client.force_login(self.staff)
        response = self.client.get(
            reverse("admin_panel:project_stats_detail", args=[self.project.pk]) + "?format=csv"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        self.assertIn("2024001", response.content.decode("utf-8-sig"))

    # ---------- 备份记录 ----------

    def test_failed_backup_is_recorded(self):
        """没有可用后端时，必须在备份历史里留下失败记录"""
        BackupRecord.objects.all().delete()
        call_command("backup_and_cleanup")
        record = BackupRecord.objects.order_by("-id").first()
        self.assertIsNotNone(record)
        self.assertEqual(record.status, "failed")
        self.assertIn("远程存储", record.error_message)

    def test_backup_now_reports_failure(self):
        self.client.force_login(self.staff)
        BackupRecord.objects.all().delete()
        response = self.client.post(reverse("admin_panel:backup_now"), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(BackupRecord.objects.exists())
        body = response.content.decode("utf-8")
        self.assertIn("备份失败", body)

    def test_backup_history_filter(self):
        BackupRecord.objects.create(status="success", backend_type="s3", file_count=3)
        BackupRecord.objects.create(status="failed", backend_type="none", error_message="boom")
        self.client.force_login(self.staff)

        page = self.client.get(reverse("admin_panel:backup_history"))
        self.assertContains(page, "boom")

        page = self.client.get(reverse("admin_panel:backup_history") + "?status=failed")
        self.assertContains(page, "boom")

        page = self.client.get(reverse("admin_panel:backup_history") + "?status=success")
        self.assertNotContains(page, "boom")
