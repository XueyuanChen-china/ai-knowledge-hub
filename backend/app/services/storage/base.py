from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol, Union


@dataclass
class MultipartUploadInitResult:
    """初始化 multipart upload 后返回的最小结果。"""

    upload_id: str


@dataclass
class DownloadObjectResult:
    """流式下载对象后的结果。"""

    byte_count: int
    sha256_hex: str
    head_bytes: bytes


class StorageAdapterError(RuntimeError):
    """对象存储层统一抛出的错误。"""


class ObjectStorageAdapter(Protocol):
    """对象存储适配器协议。"""

    def initiate_multipart_upload(
        self,
        *,
        bucket_name: str,
        object_key: str,
        content_type: str = "",
    ) -> MultipartUploadInitResult:
        ...

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
        ...

    def complete_multipart_upload(
        self,
        *,
        bucket_name: str,
        object_key: str,
        upload_id: str,
        parts: List[Dict[str, Union[str, int]]],
    ) -> None:
        ...

    def abort_multipart_upload(
        self,
        *,
        bucket_name: str,
        object_key: str,
        upload_id: str,
    ) -> None:
        ...

    def list_uploaded_parts(
        self,
        *,
        bucket_name: str,
        object_key: str,
        upload_id: str,
    ) -> List[Dict[str, Union[str, int]]]:
        ...
    # 或者对象元信息
    def head_object(
        self,
        *,
        bucket_name: str,
        object_key: str,
    ) -> Optional[Dict[str, Union[str, int]]]:
        ...

    def get_object(
        self,
        *,
        bucket_name: str,
        object_key: str,
    ) -> bytes:
        ...

    def download_object_to_path(
        self,
        *,
        bucket_name: str,
        object_key: str,
        destination_path: str,
        head_bytes_limit: int,
        chunk_size: int = 1024 * 1024,
    ) -> DownloadObjectResult:
        ...

    def delete_object(
        self,
        *,
        bucket_name: str,
        object_key: str,
    ) -> None:
        ...
