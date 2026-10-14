"""
Django settings for assignment submission system.
"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = "django-insecure-2nuw#-h(^2obcgwh^y%8haeklql=ql)ok)8@==&*u51%^#5(67"

DEBUG = True

ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "crispy_forms",
    "crispy_bootstrap5",
    "accounts",
    "assignments",
    "admin_panel",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
]

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Custom user model
AUTH_USER_MODEL = "accounts.User"

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"

CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Storage backends config (can be overridden in admin panel)
STORAGE_BACKENDS = {
    "local": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": str(MEDIA_ROOT)},
    },
}

# ---------------------------------------------------------------------------
# 上传限制 / 分片上传
# ---------------------------------------------------------------------------
# 大文件不再整包上传：前端把文件切成 CHUNK_UPLOAD_SIZE 大小的分片逐个发送，
# 单次请求体因此恒定为分片大小，不会被请求体上限卡住。
#
# 关于下面两个限制的实际情况（已实测确认）：
#   * DATA_UPLOAD_MAX_MEMORY_SIZE 只在读取「原始请求体」(request.body) 时校验，
#     multipart/form-data 的文件上传走 MultiPartParser，不经过这个校验；
#   * FILE_UPLOAD_MAX_MEMORY_SIZE 只决定上传内容放内存还是落临时文件，不是上限。
#   * 真正容易卡住大文件的是反向代理，例如 nginx 默认 client_max_body_size 1m。
#     若部署在 nginx 后面，请把 client_max_body_size 调到不小于 CHUNK_UPLOAD_SIZE。
DATA_UPLOAD_MAX_MEMORY_SIZE = 64 * 1024 * 1024          # 64 MB（原始请求体上限）
FILE_UPLOAD_MAX_MEMORY_SIZE = 8 * 1024 * 1024           # 8 MB 以上直接落盘，不进内存

# 客户端切片大小（字节）
CHUNK_UPLOAD_SIZE = 4 * 1024 * 1024                     # 4 MB

# 分片临时存放目录（相对 MEDIA_ROOT）
CHUNK_UPLOAD_TMP_DIR = "_chunk_uploads"

# 允许上传为「作业图片」的扩展名
PROJECT_IMAGE_EXTENSIONS = [".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"]