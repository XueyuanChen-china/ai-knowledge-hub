from datetime import datetime
from typing import Any, Optional

from sqlmodel import SQLModel


class ChatRequest(SQLModel):
    knowledge_base_id: int
    question: str
    thread_id: Optional[str] = None
    retrieve_top_k: int = 5


class ChatResumeRequest(SQLModel):
    thread_id: str
    approved: bool
    human_note: str = ""
    retrieve_top_k: int = 5


class ChatRunResponse(SQLModel):
    status: str
    thread_id: str
    conversation_id: Optional[int] = None
    route: str = ""
    route_reason: str = ""
    answer: str = ""
    citations: list[dict[str, Any]] = []
    need_human_review: bool = False
    review_reason: str = ""
    review_payload: Optional[dict[str, Any]] = None
    docs_preview: str = ""
    relevance_decision: str = ""
    retrieval_hit_count: int = 0
    answer_used_fallback: Optional[bool] = None
    node_trace: list[str] = []


class ChatInterruptInfo(SQLModel):
    thread_id: str
    conversation_id: Optional[int] = None
    review_task_id: Optional[int] = None
    payload: dict[str, Any]
    created_at: datetime
