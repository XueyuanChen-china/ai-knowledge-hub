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
    router_context: dict
    rewrite_context: dict
    answer_context: dict
    organization_id: int
    user_id: int
    role: str
    knowledge_base_id: int
    conversation_id: int
    thread_id: str
    review_task_id: int

    route: str
    route_reason: str
    previous_citations: list[dict]
    rewrite_queries: list[str]
    rewrite_decision: str
    rewrite_reason: str
    tool_call: dict
    tool_results: list[dict]
    tool_result_refs: list[dict]
    used_tool_result_refs: list[str]
    tool_citations: list[dict]
    tool_error: str
    tool_used: bool
    tool_call_count: int
    tool_planner_mode: str
    history_tool_results: list[dict]
    relevant_history: list[dict]
    context_gap: dict
    context_recovery_actions: list[dict]
    history_recovery_used: bool

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
