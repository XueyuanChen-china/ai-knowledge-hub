from datetime import datetime
from typing import List, Optional

from sqlmodel import SQLModel


class UploadInitRequest(SQLModel):
    """初始化上传任务的请求体。"""

    knowledge_base_id: int
    filename: str
    file_size: int
    client_mime_type: str = ""
    file_sha256: str = ""
    created_by: str = ""
    auto_create_document: Optional[bool] = None
    auto_index_on_complete: Optional[bool] = None


class UploadInitResponse(SQLModel):
    """初始化上传任务的响应体。"""

    upload_id: str
    storage_provider: str
    bucket_name: str
    object_key: str
    part_size: int
    total_parts: int
    status: str
    expires_at: datetime


class UploadTaskRead(SQLModel):
    """上传任务详情响应。"""

    upload_id: str
    knowledge_base_id: int
    original_filename: str
    storage_provider: str
    bucket_name: str
    object_key: str
    file_type: str
    client_mime_type: str
    detected_mime_type: str
    file_size: int
    part_size: int
    total_parts: int
    file_sha256: str
    storage_upload_id: str
    status: str
    completed_parts: int
    expires_at: datetime
    auto_create_document: bool
    auto_index_on_complete: bool
    document_id: Optional[int] = None
    processing_status: str
    processing_error_message: str
    error_message: str
    created_by: str
    created_at: datetime
    updated_at: datetime


class UploadPartPresignRequest(SQLModel):
    """申请单个 part 的预签名上传 URL。"""

    part_number: int


class UploadPartPresignResponse(SQLModel):
    """单个 part 的预签名上传 URL。"""

    upload_id: str
    part_number: int
    presigned_url: str
    expire_seconds: int
    status: str


class UploadBatchPresignRequest(SQLModel):
    """批量申请 part 的预签名上传 URL。"""

    part_numbers: List[int]


class UploadBatchPresignItem(SQLModel):
    """批量 presign 的单个返回项。"""

    part_number: int
    presigned_url: str


class UploadBatchPresignResponse(SQLModel):
    """批量 presign 响应。"""

    upload_id: str
    expire_seconds: int
    recommended_parallelism: int
    items: List[UploadBatchPresignItem]
    status: str


class UploadPartCompleteRequest(SQLModel):
    """回写某个 part 已上传完成。"""

    part_number: int
    etag: str = ""
    part_size: Optional[int] = None
    part_sha256: str = ""


class UploadPartRecord(SQLModel):
    """单个 part 的查询结果。"""

    part_number: int
    etag: str
    part_size: int
    part_sha256: str
    status: str
    source: str
    retry_count: int = 0
    last_error_message: str = ""
    updated_at: Optional[datetime] = None


class UploadPartsListResponse(SQLModel):
    """上传任务的 part 列表。"""

    upload_id: str
    status: str
    total_parts: int
    completed_parts: int
    local_parts: List[UploadPartRecord]
    remote_parts: List[UploadPartRecord]
    missing_part_numbers: List[int]


class UploadPartCompleteResponse(SQLModel):
    """part 回写完成响应。"""

    upload_id: str
    part_number: int
    status: str
    completed_parts: int
    detail: str


class UploadCompleteRequest(SQLModel):
    """完成整个 multipart upload 的请求。"""

    expected_total_parts: Optional[int] = None


class UploadCompleteResponse(SQLModel):
    """完成上传响应。"""

    upload_id: str
    status: str
    detail: str
    completed_parts: int
    document_id: Optional[int] = None
    processing_job_id: Optional[int] = None
    processing_status: str = ""
    processing_error_message: str = ""


class UploadAbortResponse(SQLModel):
    """取消上传响应。"""

    upload_id: str
    status: str
    detail: str


class UploadCleanupExpiredResponse(SQLModel):
    """清理过期上传任务响应。"""

    expired_count: int
    aborted_remote_count: int


class UploadCeleryHelloRequest(SQLModel):
    """Phase B Celery hello task 请求。"""

    message: str = "hello from FastAPI"


class UploadCeleryHelloResponse(SQLModel):
    """Phase B Celery hello task 投递响应。"""

    processing_job_id: int
    celery_task_id: str
    queue: str
    status: str
    current_step: str
    detail: str
