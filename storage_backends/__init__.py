"""
存储后端抽象层
支持: 本地存储、WebDAV、S3、SMB
"""
import os
from abc import ABC, abstractmethod
from datetime import datetime

from django.conf import settings


class BaseStorageBackend(ABC):
    """存储后端基类"""

    @abstractmethod
    def upload(self, local_path, remote_path):
        """上传文件到远程存储"""
        ...

    @abstractmethod
    def download(self, remote_path, local_path):
        """从远程存储下载文件"""
        ...

    @abstractmethod
    def exists(self, path):
        """检查远程路径是否存在"""
        ...

    @abstractmethod
    def delete(self, path):
        """删除远程路径"""
        ...

    @abstractmethod
    def list_files(self, prefix=""):
        """列出远程文件（返回 key 列表）"""
        ...

    def test_connection(self):
        """测试连接是否正常，返回 (bool, str)"""
        return True, "未实现连接测试，假定可用"

    def get_file_info(self, path):
        """获取文件元信息，返回 dict: {size, last_modified} 或 None"""
        return None

    def list_files_with_info(self, prefix=""):
        """列出文件并附带元信息，返回 list[dict]: {key, size, last_modified}"""
        files = self.list_files(prefix)
        result = []
        for f in files:
            info = self.get_file_info(f)
            if info is None:
                info = {"size": 0, "last_modified": None}
            result.append({"key": f, "size": info.get("size", 0), "last_modified": info.get("last_modified")})
        return result


class LocalStorageBackend(BaseStorageBackend):
    """本地存储后端（兜底）"""

    def __init__(self, base_path=None):
        self.base_path = base_path or settings.MEDIA_ROOT

    def upload(self, local_path, remote_path):
        dest = os.path.join(self.base_path, remote_path)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        import shutil
        shutil.copy2(local_path, dest)
        return dest

    def download(self, remote_path, local_path):
        src = os.path.join(self.base_path, remote_path)
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        import shutil
        shutil.copy2(src, local_path)
        return local_path

    def exists(self, path):
        return os.path.exists(os.path.join(self.base_path, path))

    def delete(self, path):
        full_path = os.path.join(self.base_path, path)
        if os.path.exists(full_path):
            os.remove(full_path)

    def list_files(self, prefix=""):
        result = []
        base = os.path.join(self.base_path, prefix)
        if os.path.exists(base):
            for root, dirs, files in os.walk(base):
                for f in files:
                    rel = os.path.relpath(os.path.join(root, f), self.base_path)
                    # 统一使用正斜杠作为 key 分隔符
                    rel = rel.replace(os.sep, "/")
                    result.append(rel)
        return result

    def test_connection(self):
        if os.path.isdir(self.base_path):
            return True, f"本地目录可用: {self.base_path}"
        return False, f"本地目录不存在: {self.base_path}"

    def get_file_info(self, path):
        full_path = os.path.join(self.base_path, path)
        if os.path.exists(full_path) and os.path.isfile(full_path):
            stat = os.stat(full_path)
            return {
                "size": stat.st_size,
                "last_modified": datetime.fromtimestamp(stat.st_mtime),
            }
        return None


class WebDAVStorageBackend(BaseStorageBackend):
    """WebDAV 存储后端"""

    def __init__(self, url, username="", password=""):
        self.url = url.rstrip("/")
        self.username = username
        self.password = password

    def _connect(self):
        try:
            import webdav3.client as wc
            options = {
                "webdav_hostname": self.url,
                "webdav_login": self.username,
                "webdav_password": self.password,
            }
            return wc.Client(options)
        except ImportError:
            raise ImportError("请安装 webdavclient3: pip install webdavclient3")

    def upload(self, local_path, remote_path):
        client = self._connect()
        # 确保远程目录存在
        parts = remote_path.split("/")
        for i in range(1, len(parts)):
            dir_path = "/".join(parts[:i])
            try:
                if not client.check(dir_path):
                    client.mkdir(dir_path)
            except Exception:
                pass
        client.upload_file(remote_path, local_path)
        return remote_path

    def download(self, remote_path, local_path):
        client = self._connect()
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        client.download_file(remote_path, local_path)
        return local_path

    def exists(self, path):
        client = self._connect()
        return client.check(path)

    def delete(self, path):
        client = self._connect()
        client.clean(path)

    def list_files(self, prefix=""):
        client = self._connect()
        try:
            return client.list(prefix)
        except Exception:
            return []

    def test_connection(self):
        try:
            client = self._connect()
            client.list("/")
            return True, f"WebDAV 连接成功: {self.url}"
        except Exception as e:
            return False, f"WebDAV 连接失败: {e}"


class S3StorageBackend(BaseStorageBackend):
    """S3 兼容对象存储后端"""

    def __init__(self, bucket, access_key, secret_key, endpoint=None, region="us-east-1"):
        self.bucket = bucket
        self.access_key = access_key
        self.secret_key = secret_key
        self.endpoint = endpoint
        self.region = region

    def _get_client(self):
        import boto3
        kwargs = {
            "aws_access_key_id": self.access_key,
            "aws_secret_access_key": self.secret_key,
            "region_name": self.region,
        }
        if self.endpoint:
            kwargs["endpoint_url"] = self.endpoint
        return boto3.client("s3", **kwargs)

    def upload(self, local_path, remote_path):
        client = self._get_client()
        # 根据扩展名推断 Content-Type
        import mimetypes
        content_type = mimetypes.guess_type(local_path)[0] or "application/octet-stream"
        client.upload_file(
            local_path,
            self.bucket,
            remote_path,
            ExtraArgs={"ContentType": content_type},
        )
        return remote_path

    def download(self, remote_path, local_path):
        client = self._get_client()
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        client.download_file(self.bucket, remote_path, local_path)
        return local_path

    def exists(self, path):
        client = self._get_client()
        try:
            client.head_object(Bucket=self.bucket, Key=path)
            return True
        except Exception:
            return False

    def delete(self, path):
        client = self._get_client()
        client.delete_object(Bucket=self.bucket, Key=path)

    def list_files(self, prefix=""):
        """列出所有匹配前缀的文件 key（自动分页，S3 单次最多 1000 条）"""
        client = self._get_client()
        keys = []
        continuation_token = None
        while True:
            kwargs = {"Bucket": self.bucket, "Prefix": prefix}
            if continuation_token:
                kwargs["ContinuationToken"] = continuation_token
            response = client.list_objects_v2(**kwargs)
            for obj in response.get("Contents", []):
                keys.append(obj["Key"])
            if response.get("IsTruncated"):
                continuation_token = response.get("NextContinuationToken")
            else:
                break
        return keys

    def list_files_with_info(self, prefix=""):
        """列出文件并附带元信息，利用 S3 list_objects_v2 已返回的元数据，避免额外请求"""
        client = self._get_client()
        result = []
        continuation_token = None
        while True:
            kwargs = {"Bucket": self.bucket, "Prefix": prefix}
            if continuation_token:
                kwargs["ContinuationToken"] = continuation_token
            response = client.list_objects_v2(**kwargs)
            for obj in response.get("Contents", []):
                result.append({
                    "key": obj["Key"],
                    "size": obj.get("Size", 0),
                    "last_modified": obj.get("LastModified"),
                })
            if response.get("IsTruncated"):
                continuation_token = response.get("NextContinuationToken")
            else:
                break
        return result

    def test_connection(self):
        """测试 S3 连接：验证凭证并尝试列出 bucket 内容"""
        if not self.bucket:
            return False, "S3 Bucket 未配置"
        if not self.access_key or not self.secret_key:
            return False, "S3 Access Key / Secret Key 未配置"
        try:
            client = self._get_client()
            # head_bucket 验证 bucket 是否存在且凭证有效
            client.head_bucket(Bucket=self.bucket)
            return True, f"S3 连接成功 (bucket: {self.bucket})"
        except Exception as e:
            return False, f"S3 连接失败: {e}"

    def get_file_info(self, path):
        client = self._get_client()
        try:
            response = client.head_object(Bucket=self.bucket, Key=path)
            return {
                "size": response.get("ContentLength", 0),
                "last_modified": response.get("LastModified"),
            }
        except Exception:
            return None


class SMBStorageBackend(BaseStorageBackend):
    """SMB 共享存储后端"""

    def __init__(self, host, share, username="", password="", domain=""):
        self.host = host
        self.share = share
        self.username = username
        self.password = password
        self.domain = domain

    def _connect(self):
        try:
            from smb.SMBConnection import SMBConnection
            conn = SMBConnection(
                self.username,
                self.password,
                "assignment_system",
                self.host,
                domain=self.domain,
                use_ntlm_v2=True,
            )
            conn.connect(self.host, 445)
            return conn
        except ImportError:
            raise ImportError("请安装 pysmb: pip install pysmb")

    def upload(self, local_path, remote_path):
        conn = self._connect()
        with open(local_path, "rb") as f:
            conn.storeFile(self.share, remote_path, f)
        conn.close()
        return remote_path

    def download(self, remote_path, local_path):
        conn = self._connect()
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        with open(local_path, "wb") as f:
            conn.retrieveFile(self.share, remote_path, f)
        conn.close()
        return local_path

    def exists(self, path):
        conn = self._connect()
        try:
            conn.getAttributes(self.share, path)
            conn.close()
            return True
        except Exception:
            conn.close()
            return False

    def delete(self, path):
        conn = self._connect()
        conn.deleteFiles(self.share, path)
        conn.close()

    def list_files(self, prefix=""):
        conn = self._connect()
        files = conn.listPath(self.share, prefix)
        conn.close()
        return [f.filename for f in files if f.filename not in (".", "..")]

    def test_connection(self):
        try:
            conn = self._connect()
            conn.listPath(self.share, "/")
            conn.close()
            return True, f"SMB 连接成功: {self.host}/{self.share}"
        except Exception as e:
            return False, f"SMB 连接失败: {e}"


def get_storage_backend(backend_type="local", settings_obj=None):
    """根据后端类型和设置返回对应的存储后端实例"""
    if settings_obj is None:
        from assignments.models import LiveSettings
        settings_obj = LiveSettings.load()

    if backend_type == "webdav":
        return WebDAVStorageBackend(
            url=settings_obj.webdav_url,
            username=settings_obj.webdav_username,
            password=settings_obj.webdav_password,
        )
    elif backend_type == "s3":
        return S3StorageBackend(
            bucket=settings_obj.s3_bucket,
            access_key=settings_obj.s3_access_key,
            secret_key=settings_obj.s3_secret_key,
            endpoint=settings_obj.s3_endpoint,
            region=settings_obj.s3_region,
        )
    elif backend_type == "smb":
        return SMBStorageBackend(
            host=settings_obj.smb_host,
            share=settings_obj.smb_share,
            username=settings_obj.smb_username,
            password=settings_obj.smb_password,
            domain=settings_obj.smb_domain,
        )
    else:
        return LocalStorageBackend()
