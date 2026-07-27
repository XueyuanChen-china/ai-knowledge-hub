from typing import TypedDict

from app.services.rag_service import RetrievedDocument


class GraphState(TypedDict, total=False):
    """Graph workflow shared state.

    Day 15 先只落最小字段集合，后面可以继续往上叠：
    - relevance_check
    - human_review
    - answer generation
    - message persistence
    """

    question: str
    organization_id: int
    knowledge_base_id: int
    conversation_id: int
    thread_id: str
    review_task_id: int

    route: str
    route_reason: str

    retrieved_docs: list[RetrievedDocument]
    retrieval_hit_count: int
    context: str
    docs_preview: str
    relevance_score: float
    relevance_decision: str
    review_reason: str

    need_human_review: bool
    human_approved: bool
    human_note: str

    answer: str
    answer_used_fallback: bool
    citations: list[dict]

    node_trace: list[str]
