from django.urls import path

from . import views

urlpatterns = [
    path("", views.home_view, name="home"),
    path("submit/<slug:slug>/", views.submit_project_view, name="submit_project"),
    path("upload/chunk/", views.chunk_upload_view, name="chunk_upload"),
    path("attachments/<int:pk>/download/", views.download_attachment_view, name="download_attachment"),
]
