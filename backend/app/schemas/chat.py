from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel
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


class RetrievedDocPreviewItem(BaseModel):
    index: int
    doc_id: Optional[int] = None
    chunk_id: Optional[int] = None
    knowledge_item_id: Optional[int] = None
    title: str
    content: str
    content_preview: str
    score: float
    metadata: dict[str, Any] = {}


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
    retrieved_docs_preview_items: list[RetrievedDocPreviewItem] = []
    relevance_decision: str = ""
    retrieval_hit_count: int = 0
    answer_used_fallback: Optional[bool] = None
    tool_used: bool = False
    tool_results: list[dict[str, Any]] = []
    tool_error: str = ""
    tool_call_count: int = 0
    tool_planner_mode: str = ""
    context_gap: dict[str, Any] = {}
    history_recovery_used: bool = False
    relevant_history: list[dict[str, Any]] = []
    node_trace: list[str] = []


class ChatInterruptInfo(SQLModel):
    thread_id: str
    conversation_id: Optional[int] = None
    review_task_id: Optional[int] = None
    payload: dict[str, Any]
    created_at: datetime


class ConversationSummaryResponse(SQLModel):
    id: int
    knowledge_base_id: int
    title: str
    thread_id: str
    is_pinned: bool = False
    created_at: datetime
    updated_at: datetime
    message_count: int = 0
    last_message_preview: str = ""
    last_message_role: str = ""


class ConversationUpdateRequest(SQLModel):
    title: Optional[str] = None
    is_pinned: Optional[bool] = None


class ConversationMessageResponse(SQLModel):
    id: int
    conversation_id: int
    role: str
    content: str
    citations: list[dict[str, Any]] = []
    created_at: datetime
