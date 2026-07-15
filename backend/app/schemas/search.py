from typing import Any, Optional

from pydantic import BaseModel, Field


class SemanticSearchRequest(BaseModel):
    """语义搜索请求。"""

    knowledge_base_id: int
    query: str
    top_k: int = Field(default=5, ge=1, le=20)


class SemanticSearchResult(BaseModel):
    """语义搜索返回的单条结果。"""

    doc_id: Optional[int]
    chunk_id: Optional[int]
    title: str
    content_preview: str
    score: float
    metadata: dict[str, Any]
