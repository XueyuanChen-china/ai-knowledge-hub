from datetime import datetime
from typing import Optional

from sqlmodel import SQLModel


class KnowledgeItemCreate(SQLModel):
    """手动创建知识条目时的请求体。

    Day 4 先支持手动录入知识；从文档自动生成知识条目后续再接入。
    """

    knowledge_base_id: int
    title: str
    content: str
    tags: str = ""
    status: str = "draft"


class KnowledgeItemUpdate(SQLModel):
    """编辑知识条目时的请求体。

    Day 4 使用 PUT，所以这里按完整更新处理。
    """

    knowledge_base_id: int
    title: str
    content: str
    tags: str = ""
    status: str = "draft"


class KnowledgeItemRead(SQLModel):
    """返回给前端的知识条目数据。"""

    id: int
    knowledge_base_id: int
    title: str
    content: str
    tags: str
    status: str
    source_type: str
    source_document_id: Optional[int]
    created_at: datetime
    updated_at: datetime


class KnowledgeItemChunkResponse(SQLModel):
    """知识条目切分后的响应。"""

    knowledge_item_id: int
    chunk_count: int


class KnowledgeItemIndexResponse(SQLModel):
    """知识条目完成切片并写入向量库后的响应。"""

    knowledge_item_id: int
    chunk_count: int
    vector_count: int
    index_name: str
