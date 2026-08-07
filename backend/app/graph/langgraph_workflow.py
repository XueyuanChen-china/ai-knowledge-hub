from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlmodel import Session

from app.graph import nodes
from app.graph.checkpointer import get_graph_checkpointer
from app.graph.state import GraphState


def build_checkpointed_workflow(
    session: Session,
    *,
    retrieve_top_k: int = 5,
):
    """构造带 PostgreSQL checkpointer 的 LangGraph 工作流。"""

    builder = StateGraph(GraphState)

    builder.add_node("router", lambda state: nodes.router_node(state))
    builder.add_node(
        "direct",
        lambda state: nodes.direct_answer_node(state),
    )
    builder.add_node(
        "context_gap_check",
        lambda state: nodes.context_gap_check_node(state),
    )
    builder.add_node(
        "history_recovery",
        lambda state: nodes.history_recovery_node(state, session),
    )
    builder.add_node(
        "query_rewrite",
        lambda state: nodes.query_rewrite_node(state),
    )
    builder.add_node(
        "retrieve",
        lambda state: nodes.retrieve_node(
            state,
            session,
            top_k=retrieve_top_k,
        ),
    )
    builder.add_node(
        "tool_decision",
        lambda state: nodes.tool_decision_node(state),
    )
    builder.add_node(
        "tool_call",
        lambda state: nodes.tool_call_node(state, session),
    )
    builder.add_node(
        "relevance_check",
        lambda state: nodes.relevance_check_node(state),
    )
    builder.add_node("human_review", lambda state: human_review_node(state))
    builder.add_node("answer", lambda state: nodes.answer_node(state))
    builder.add_node(
        "review_rejected",
        lambda state: nodes.review_rejected_node(state),
    )
    builder.add_node(
        "complex",
        lambda state: nodes.complex_answer_node(state),
    )

    builder.add_edge(START, "router")
    builder.add_conditional_edges(
        "router",
        route_after_router,
        {
            nodes.DIRECT_ROUTE: "direct",
            nodes.RAG_ROUTE: "context_gap_check",
            nodes.COMPLEX_ROUTE: "complex",
            nodes.TOOL_ROUTE: "tool_decision",
        },
    )
    builder.add_edge("context_gap_check", "history_recovery")
    builder.add_edge("history_recovery", "query_rewrite")
    builder.add_edge("query_rewrite", "retrieve")
    builder.add_edge("direct", END)
    builder.add_edge("retrieve", "tool_decision")
    builder.add_edge("tool_decision", "tool_call")
    builder.add_conditional_edges(
        "tool_call",
        route_after_tool_call,
        {
            "answer": "answer",
            "relevance_check": "relevance_check",
            "human_review": "human_review",
        },
    )
    builder.add_conditional_edges(
        "relevance_check",
        route_after_relevance_check,
        {
            "answer": "answer",
            "human_review": "human_review",
        },
    )
    builder.add_conditional_edges(
        "human_review",
        route_after_human_review,
        {
            "answer": "answer",
            "review_rejected": "review_rejected",
        },
    )
    builder.add_edge("answer", END)
    builder.add_edge("review_rejected", END)
    builder.add_edge("complex", END)

    return builder.compile(checkpointer=get_graph_checkpointer())


def route_after_router(state: GraphState) -> str:
    route = str(state.get("route") or "").strip().lower()
    if route == nodes.DIRECT_ROUTE:
        return nodes.DIRECT_ROUTE
    if route == nodes.COMPLEX_ROUTE:
        return nodes.COMPLEX_ROUTE
    if route == nodes.TOOL_ROUTE:
        return nodes.TOOL_ROUTE
    return nodes.RAG_ROUTE


def route_after_tool_call(state: GraphState) -> str:
    """区分初次 RAG 的工具增强和上一轮引用的直接工具路线。"""

    route = str(state.get("route") or "")
    if route == nodes.TOOL_ROUTE:
        if state.get("tool_error"):
            return "human_review"
        return "answer"
    return "relevance_check"


def route_after_relevance_check(state: GraphState) -> str:
    if state.get("need_human_review"):
        return "human_review"
    return "answer"


def route_after_human_review(state: GraphState) -> str:
    if state.get("human_approved"):
        return "answer"
    return "review_rejected"


def human_review_node(state: GraphState) -> GraphState:
    """Day 20 Human Review Node。

    第一次进入时触发 interrupt，暂停等待人工输入。
    恢复时读取 Command(resume=...) 里的值，再写回审核结果。
    """

    payload = build_review_payload(state)
    resume_value = interrupt(payload)
    approved, human_note = normalize_resume_value(resume_value)

    updated_state = nodes.human_review_result_node(
        state,
        approved=approved,
        human_note=human_note,
    )
    updated_state["node_trace"] = nodes.append_trace(
        state.get("node_trace"),
        ["human_review"],
    )
    return updated_state


def build_review_payload(state: GraphState) -> dict:
    """构造 interrupt 时抛给外部的审核载荷。"""

    return {
        "question": str(state.get("question") or ""),
        "thread_id": str(state.get("thread_id") or ""),
        "route": str(state.get("route") or ""),
        "docs_preview": str(state.get("docs_preview") or ""),
        "retrieval_hit_count": int(state.get("retrieval_hit_count") or 0),
        "relevance_score": float(state.get("relevance_score") or 0.0),
        "review_reason": str(state.get("review_reason") or ""),
        "citations": list(state.get("citations") or []),
    }


def normalize_resume_value(resume_value: object) -> tuple[bool, str]:
    """把 Command(resume=...) 的值统一归一化。"""

    if isinstance(resume_value, dict):
        approved = bool(resume_value.get("approved"))
        human_note = str(resume_value.get("human_note") or "").strip()
        return approved, human_note

    if isinstance(resume_value, bool):
        return resume_value, ""

    return False, str(resume_value or "").strip()


def get_thread_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def get_checkpoint_snapshot(session: Session, thread_id: str):
    """拿当前 thread 的 checkpoint 快照。"""

    graph = build_checkpointed_workflow(session)
    return get_checkpoint_snapshot_with_graph(graph, thread_id)


def get_checkpoint_snapshot_with_graph(graph, thread_id: str):
    """从已构建 graph 上读取 checkpoint 快照。"""

    return graph.get_state(get_thread_config(thread_id))


def invoke_graph(
    graph,
    *,
    state: GraphState,
    thread_id: str,
):
    """启动一次新的图执行。"""

    return graph.invoke(
        state,
        config=get_thread_config(thread_id),
    )


def resume_graph(
    graph,
    *,
    thread_id: str,
    approved: bool,
    human_note: str = "",
):
    """从 interrupt 位置恢复执行。"""

    return graph.invoke(
        Command(
            resume={
                "approved": approved,
                "human_note": human_note,
            }
        ),
        config=get_thread_config(thread_id),
    )
