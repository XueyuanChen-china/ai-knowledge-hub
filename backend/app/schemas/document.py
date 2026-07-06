from datetime import datetime

from sqlmodel import SQLModel


class DocumentRead(SQLModel):
    """返回给前端的文档数据。

    Day 5 上传文件成功后，会返回 document_id，也会返回完整的文档记录。
    """

    id: int
    knowledge_base_id: int
    filename: str
    file_path: str
    file_type: str
    status: str
    extracted_text: str
    created_at: datetime


class DocumentChunkResponse(SQLModel):
    """文档切分后的响应。"""

    document_id: int
    knowledge_item_id: int
    chunk_count: int


class DocumentIndexResponse(SQLModel):
    """文档完成切片并写入向量库后的响应。"""

    document_id: int
    knowledge_item_id: int
    chunk_count: int
    vector_count: int
    index_name: str
