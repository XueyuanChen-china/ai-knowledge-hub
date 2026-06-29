from datetime import datetime
from typing import Optional

from sqlmodel import SQLModel


class ChunkRead(SQLModel):
    """返回给前端查看的 chunk 数据。"""

    id: int
    knowledge_base_id: int
    document_id: Optional[int]
    knowledge_item_id: int
    chunk_index: int
    content: str
    vector_id: Optional[str]
    metadata_json: str
    created_at: datetime
