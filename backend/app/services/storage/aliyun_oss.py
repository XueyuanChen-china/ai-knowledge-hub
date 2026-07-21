from hashlib import sha256
from typing import Any, Dict, List, Optional, Union

from app.config import Settings
from app.services.storage.base import (
    DownloadObjectResult,
    MultipartUploadInitResult,
    ObjectStorageAdapter,
    StorageAdapterError,
)


class AliyunOSSStorageAdapter(ObjectStorageAdapter):
    """阿里云 OSS 适配器。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        try:
            import oss2
        except ImportError as exc:  # pragma: no cover - 依赖缺失属于部署问题
            raise RuntimeError("oss2 is required for Aliyun OSS support") from exc

        self._oss2 = oss2
        auth = oss2.Auth(
            settings.oss_access_key_id,
            settings.oss_access_key_secret,
        )
        endpoint = settings.oss_endpoint
        if not endpoint.startswith(("http://", "https://")):
            endpoint = f"https://{endpoint}"

        self._bucket = oss2.Bucket(auth, endpoint, settings.oss_bucket)

    def _ensure_bucket(self, bucket_name: str) -> None:
        if bucket_name != self.settings.oss_bucket:
            raise StorageAdapterError(
                f"Unexpected bucket name: {bucket_name}"
            )

    def initiate_multipart_upload(
        self,
        *,
        bucket_name: str,
        object_key: str,
        content_type: str = "",
    ) -> MultipartUploadInitResult:
        self._ensure_bucket(bucket_name)
        headers: dict[str, str] = {}
        if content_type.strip():
            headers["Content-Type"] = content_type.strip()

        try:
            result = self._bucket.init_multipart_upload(
                object_key,
                headers=headers or None,
            )
        except Exception as exc:  # pragma: no cover - 依赖真实 OSS
            raise StorageAdapterError("Failed to initiate OSS multipart upload") from exc

        return MultipartUploadInitResult(upload_id=result.upload_id)

    def generate_upload_part_presigned_url(
        self,
        *,
        bucket_name: str,
        object_key: str,
        upload_id: str,
        part_number: int,
        expire_seconds: int,
        content_type: str = "",
    ) -> str:
        self._ensure_bucket(bucket_name)
        try:
            headers = {"Content-Type": content_type} if content_type.strip() else None
            return self._bucket.sign_url(
                "PUT",
                object_key,
                expire_seconds,
                params={
                    "uploadId": upload_id,
                    "partNumber": str(part_number),
                },
                headers=headers,
            )
        except Exception as exc:  # pragma: no cover
            raise StorageAdapterError("Failed to generate OSS part presigned URL") from exc

    def complete_multipart_upload(
        self,
        *,
        bucket_name: str,
        object_key: str,
        upload_id: str,
        parts: List[Dict[str, Union[str, int]]],
    ) -> None:
        self._ensure_bucket(bucket_name)
        try:
            part_infos = [
                self._oss2.models.PartInfo(
                    int(part["part_number"]),
                    str(part["etag"]),
                )
                for part in parts
            ]
            self._bucket.complete_multipart_upload(
                object_key,
                upload_id,
                part_infos,
            )
        except Exception as exc:  # pragma: no cover
            raise StorageAdapterError("Failed to complete OSS multipart upload") from exc

    def abort_multipart_upload(
        self,
        *,
        bucket_name: str,
        object_key: str,
        upload_id: str,
    ) -> None:
        self._ensure_bucket(bucket_name)
        try:
            self._bucket.abort_multipart_upload(object_key, upload_id)
        except Exception as exc:  # pragma: no cover
            raise StorageAdapterError("Failed to abort OSS multipart upload") from exc

    def list_uploaded_parts(
        self,
        *,
        bucket_name: str,
        object_key: str,
        upload_id: str,
    ) -> List[Dict[str, Union[str, int]]]:
        self._ensure_bucket(bucket_name)
        try:
            result = self._bucket.list_parts(object_key, upload_id)
        except Exception as exc:  # pragma: no cover
            raise StorageAdapterError("Failed to list OSS uploaded parts") from exc

        return [
            {
                "part_number": int(part.part_number),
                "etag": str(part.etag),
                "size": int(part.size),
            }
            for part in result.parts
        ]

    def head_object(
        self,
        *,
        bucket_name: str,
        object_key: str,
    ) -> Optional[Dict[str, Union[str, int]]]:
        self._ensure_bucket(bucket_name)
        try:
            result: Any = self._bucket.head_object(object_key)
        except self._oss2.exceptions.NoSuchKey:  # pragma: no cover
            return None
        except Exception as exc:  # pragma: no cover
            raise StorageAdapterError("Failed to head OSS object") from exc

        return {
            "content_length": int(result.content_length),
            "etag": str(result.etag),
            "content_type": str(result.content_type or ""),
        }

    def get_object(
        self,
        *,
        bucket_name: str,
        object_key: str,
    ) -> bytes:
        self._ensure_bucket(bucket_name)
        try:
            result = self._bucket.get_object(object_key)
            return result.read()
        except Exception as exc:  # pragma: no cover
            raise StorageAdapterError("Failed to get OSS object") from exc

    def download_object_to_path(
        self,
        *,
        bucket_name: str,
        object_key: str,
        destination_path: str,
        head_bytes_limit: int,
        chunk_size: int = 1024 * 1024,
    ) -> DownloadObjectResult:
        self._ensure_bucket(bucket_name)
        hasher = sha256()
        head_buffer = bytearray()
        byte_count = 0
        try:
            result = self._bucket.get_object(object_key)
            with open(destination_path, "wb") as output_file:
                while True:
                    chunk = result.read(chunk_size)
                    if not chunk:
                        break
                    output_file.write(chunk)
                    hasher.update(chunk)
                    byte_count += len(chunk)
                    if len(head_buffer) < head_bytes_limit:
                        remaining = head_bytes_limit - len(head_buffer)
                        head_buffer.extend(chunk[:remaining])
        except Exception as exc:  # pragma: no cover
            raise StorageAdapterError("Failed to stream OSS object to path") from exc

        return DownloadObjectResult(
            byte_count=byte_count,
            sha256_hex=hasher.hexdigest(),
            head_bytes=bytes(head_buffer),
        )

    def delete_object(
        self,
        *,
        bucket_name: str,
        object_key: str,
    ) -> None:
        self._ensure_bucket(bucket_name)
        try:
            self._bucket.delete_object(object_key)
        except Exception as exc:  # pragma: no cover
            raise StorageAdapterError("Failed to delete OSS object") from exc
