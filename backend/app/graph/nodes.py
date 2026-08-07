import re
from typing import Optional

from sqlmodel import Session, select

from app.agent_tools.audit import record_tool_call_audit
from app.agent_tools.registry import (
    execute_readonly_tool,
    plan_conversation_history_tool,
    plan_readonly_tool,
    plan_readonly_tool_with_llm,
)
from app.agent_tools.schemas import ToolCallRequest, ToolExecutionContext, ToolExecutionResult
from app.config import get_settings
from app.db.models import KnowledgeBase
from app.graph.state import GraphState
from app.observability.context import get_request_id, get_trace_id
from app.observability.metrics import get_metrics
from app.services import (
    context_manager,
    llm_answer_service,
    llm_router_service,
    query_rewrite_service,
    rag_service,
)
from app.services.context_gap_detector import detect_context_gap
from app.services.context_types import ContextItem, RecoveryAction

START_NODE = "START"
ROUTER_NODE = "router"
DIRECT_NODE = "direct"
RETRIEVE_NODE = "retrieve"
QUERY_REWRITE_NODE = "query_rewrite"
CONTEXT_GAP_CHECK_NODE = "context_gap_check"
HISTORY_RECOVERY_NODE = "history_recovery"
TOOL_DECISION_NODE = "tool_decision"
TOOL_CALL_NODE = "tool_call"
RELEVANCE_CHECK_NODE = "relevance_check"
REVIEW_NODE = "review"
ANSWER_NODE = "answer"
COMPLEX_NODE = "complex"
END_NODE = "END"

DIRECT_ROUTE = "direct"
RAG_ROUTE = "rag"
COMPLEX_ROUTE = "complex"
TOOL_ROUTE = "tool"

TOOL_INTENT_MARKERS = (
    "前后文",
    "上下文",
    "相邻",
    "上一段",
    "下一段",
    "展开原文",
    "完整原文",
    "全文",
    "详细内容",
    "整份文档",
    "有哪些文档",
    "文档列表",
    "所有文档",
    "文件列表",
    "知识条目详情",
    "条目内容",
    "这个知识条目",
)

DIRECT_GREETING_PATTERNS = (
    r"^(你好|您好|hi|hello)\b",
    r"^(早上好|中午好|下午好|晚上好)",
)

DIRECT_CONCEPT_PATTERNS = (
    r"^什么是\s*rag[？?]?$",
    r"^什么是\s*agent[？?]?$",
    r"^什么是\s*docker[？?]?$",
    r"^什么是\s*langgraph[？?]?$",
)

COMPLEX_PATTERNS = (
    r"^(总结|总结一下|概括|归纳|梳理|整理|分析).*(知识库|文档|重点|内容)",
    r"^(对比|比较).*(知识库|文档|制度|方案)",
    r"^(请)?总结这个知识库的重点",
)

def router_node(state: GraphState) -> GraphState:
    """Day 16 Router：先尝试 LLM，再用规则兜底。"""

    question = str(state.get("question") or "").strip()
    if not question:
        raise ValueError("question must not be empty")

    knowledge_base_id = state.get("knowledge_base_id")
    router_context = dict(state.get("router_context") or {})
    router_context["previous_citations"] = list(state.get("previous_citations") or [])
    llm_decision = llm_router_service.route_question_with_llm(
        question,
        knowledge_base_id,
        conversation_context=router_context,
    )
    if llm_decision is not None:
        route = llm_decision.route
        route_reason = llm_decision.reason
    else:
        route, route_reason = route_question(
            question,
            knowledge_base_id,
            previous_citations=list(state.get("previous_citations") or []),
        )

    updated_state = dict(state)
    updated_state["route"] = route
    updated_state["route_reason"] = route_reason
    updated_state["node_trace"] = append_trace(state.get("node_trace"), [START_NODE, ROUTER_NODE])
    return updated_state


def query_rewrite_node(state: GraphState) -> GraphState:
    """在 RAG 检索前按需生成补充查询。

    明确问题只保留原始 query；只有当前问题依赖历史上下文时才调用 LLM。
    """

    question = str(state.get("question") or "").strip()
    if not question:
        raise ValueError("question must not be empty")

    rewrite_context = dict(state.get("rewrite_context") or {})
    recent_messages = list(rewrite_context.get("recent_messages") or [])
    # 历史恢复结果作为“历史消息”补充给 Rewrite，但不覆盖原始近期消息。
    for item in list(state.get("relevant_history") or []):
        if isinstance(item, dict) and item.get("content"):
            recent_messages.append(
                {
                    "role": "history",
                    "content": str(item.get("content") or ""),
                }
            )
    decision = query_rewrite_service.decide_query_rewrite(question, recent_messages)
    queries = [question]
    if decision.need_rewrite:
        rewritten_queries = query_rewrite_service.rewrite_question_with_llm(
            question,
            recent_messages,
        )
        if rewritten_queries:
            queries = rewritten_queries

    updated_state = dict(state)
    updated_state["rewrite_queries"] = queries
    updated_state["rewrite_decision"] = "rewritten" if len(queries) > 1 else "skipped"
    updated_state["rewrite_reason"] = decision.reason
    updated_state["node_trace"] = append_trace(
        state.get("node_trace"),
        [QUERY_REWRITE_NODE],
    )
    return updated_state


def context_gap_check_node(state: GraphState) -> GraphState:
    """判断当前问题是否需要从当前会话恢复历史上下文。"""

    context = dict(
        state.get("rewrite_context")
        or state.get("router_context")
        or state.get("answer_context")
        or {}
    )
    decision = detect_context_gap(str(state.get("question") or ""), context)
    get_metrics().record_context_recovery(
        "needed" if decision.need_recovery else "not_needed"
    )
    updated_state = dict(state)
    updated_state["context_gap"] = decision.to_dict()
    updated_state["history_recovery_used"] = False
    updated_state["node_trace"] = append_trace(
        state.get("node_trace"),
        [CONTEXT_GAP_CHECK_NODE],
    )
    return updated_state


def history_recovery_node(state: GraphState, session: Session) -> GraphState:
    """按需查询当前会话历史，并把结果放回三个 Context Pack。"""

    decision = dict(state.get("context_gap") or {})
    updated_state = dict(state)
    if not bool(decision.get("need_recovery")):
        updated_state["relevant_history"] = list(state.get("relevant_history") or [])
        updated_state["context_recovery_actions"] = list(
            state.get("context_recovery_actions") or []
        )
        updated_state["node_trace"] = append_trace(
            state.get("node_trace"),
            [HISTORY_RECOVERY_NODE],
        )
        return updated_state

    request = plan_conversation_history_tool(
        str(state.get("question") or ""),
        reason=str(decision.get("reason") or "context gap detected"),
    )
    try:
        context = ToolExecutionContext(
            organization_id=int(state.get("organization_id") or 0),
            knowledge_base_id=int(state.get("knowledge_base_id") or 0),
            user_id=int(state.get("user_id")) if state.get("user_id") else None,
            role=str(state.get("role") or "viewer"),
            conversation_id=(
                int(state.get("conversation_id"))
                if state.get("conversation_id")
                else None
            ),
            request_id=get_request_id(),
            trace_id=get_trace_id(),
        )
        result = execute_readonly_tool(request, context=context, session=session)
    except Exception as exc:
        result = ToolExecutionResult(
            tool_name=request.name,
            ok=False,
            error_code="history_recovery_error",
            error_message="history recovery failed",
        )
        _ = exc

    result_payload = _model_dump(result)
    updated_state["history_tool_results"] = [result_payload]
    updated_state["history_recovery_used"] = True
    updated_state["tool_call_count"] = int(state.get("tool_call_count") or 0) + 1

    recovered_items: list[dict] = []
    if result.ok:
        data = result.data if isinstance(result.data, dict) else {}
        for raw_message in list(data.get("messages") or []):
            if not isinstance(raw_message, dict) or not raw_message.get("content"):
                continue
            recovered_items.append(
                ContextItem(
                    kind="relevant_history",
                    content=(
                        f"{raw_message.get('role') or 'history'}: "
                        f"{raw_message.get('content') or ''}"
                    ),
                    source_ids=[str(raw_message.get("message_id") or "")],
                    importance=float(raw_message.get("score") or 0.0),
                    metadata={
                        "message_id": raw_message.get("message_id"),
                        "created_at": raw_message.get("created_at") or "",
                        "matched_terms": raw_message.get("matched_terms") or [],
                    },
                ).to_dict()
            )

    updated_state["relevant_history"] = recovered_items
    action = RecoveryAction(
        action="search_conversation_history",
        reason=str(decision.get("reason") or "context gap detected"),
        success=bool(result.ok),
        source_ids=[
            str(item.get("source_ids", [""])[0])
            for item in recovered_items
            if item.get("source_ids")
        ],
    )
    updated_state["context_recovery_actions"] = [action.to_dict()]
    get_metrics().record_context_recovery("success" if result.ok else "failed")
    for context_name in ("router_context", "rewrite_context", "answer_context"):
        context_payload = dict(state.get(context_name) or {})
        context_payload["relevant_history"] = recovered_items
        context_payload["recovery_actions"] = [action.to_dict()]
        updated_state[context_name] = context_payload
    updated_state["node_trace"] = append_trace(
        state.get("node_trace"),
        [HISTORY_RECOVERY_NODE],
    )
    return updated_state


def direct_answer_node(state: GraphState) -> GraphState:
    """direct 分支当前先不检索知识库。"""

    question = str(state.get("question") or "").strip()
    if not question:
        raise ValueError("question must not be empty")

    updated_state = dict(state)
    updated_state["answer"] = (
        "这是一个 direct 问题，当前基础图不会进入知识库检索。"
        "后续会把这里升级成真正的 direct answer 节点。"
    )
    updated_state["retrieved_docs"] = []
    updated_state["context"] = ""
    updated_state["docs_preview"] = ""
    updated_state["citations"] = []
    updated_state["answer_used_fallback"] = False
    updated_state["need_human_review"] = False
    updated_state["relevance_decision"] = "direct"
    updated_state["review_reason"] = ""
    updated_state["node_trace"] = append_trace(state.get("node_trace"), [DIRECT_NODE, END_NODE])
    return updated_state


def complex_answer_node(state: GraphState) -> GraphState:
    """complex 分支先占位，后面再扩成多步 workflow。"""

    question = str(state.get("question") or "").strip()
    if not question:
        raise ValueError("question must not be empty")

    updated_state = dict(state)
    updated_state["answer"] = (
        "这是一个 complex 问题，当前会先识别出来，但还没有进入多轮检索 / 总结工作流。"
        "后续可以继续扩成 retrieve -> rerank -> summarize。"
    )
    updated_state["retrieved_docs"] = []
    updated_state["context"] = ""
    updated_state["docs_preview"] = ""
    updated_state["citations"] = []
    updated_state["answer_used_fallback"] = False
    updated_state["need_human_review"] = False
    updated_state["relevance_decision"] = "complex"
    updated_state["review_reason"] = ""
    updated_state["node_trace"] = append_trace(state.get("node_trace"), [COMPLEX_NODE, END_NODE])
    return updated_state


def retrieve_node(
    state: GraphState,
    session: Session,
    *,
    top_k: int = 5,
) -> GraphState:
    """Day 17 Retrieve Node。

    职责：
    - 调 Elasticsearch 向量检索
    - 写回 retrieved_docs
    - 写回 context / docs_preview / citations
    """

    question = str(state.get("question") or "").strip()
    if not question:
        raise ValueError("question must not be empty")

    knowledge_base_id = state.get("knowledge_base_id")
    if knowledge_base_id is None:
        raise ValueError("knowledge_base_id is required for rag route")
    organization_id = state.get("organization_id")
    retrieve_kwargs = {"top_k": top_k}
    if organization_id is not None:
        retrieve_kwargs["organization_id"] = int(organization_id)
    query_variants = [
        str(item).strip()
        for item in list(state.get("rewrite_queries") or [])
        if str(item).strip() and str(item).strip() != question
    ]
    if query_variants:
        retrieve_kwargs["query_variants"] = query_variants
    retrieved_docs = rag_service.retrieve(
        question,
        int(knowledge_base_id),
        session,
        **retrieve_kwargs,
    )
    context = rag_service.format_context(retrieved_docs)

    updated_state = dict(state)
    updated_state["retrieved_docs"] = retrieved_docs
    updated_state["retrieval_hit_count"] = len(retrieved_docs)
    updated_state["context"] = context
    updated_state["docs_preview"] = build_docs_preview(retrieved_docs)
    updated_state["citations"] = rag_service.build_citations(retrieved_docs)
    updated_state["relevance_score"] = max(
        (get_document_relevance_score(document) for document in retrieved_docs),
        default=0.0,
    )
    updated_state["node_trace"] = append_trace(state.get("node_trace"), [RETRIEVE_NODE])
    return updated_state


def rag_retrieve_node(
    state: GraphState,
    session: Session,
    *,
    top_k: int = 5,
) -> GraphState:
    """兼容旧名字，内部直接转到 retrieve_node。"""

    return retrieve_node(
        state,
        session,
        top_k=top_k,
    )


def tool_decision_node(state: GraphState) -> GraphState:
    """让 Qwen 原生选择只读工具，模型不可用时使用规则兜底。"""

    question = str(state.get("question") or "")
    retrieved_docs = list(state.get("retrieved_docs") or [])
    previous_citations = list(state.get("previous_citations") or [])
    normalized = normalize_question(question)
    should_plan = str(state.get("route") or "") == TOOL_ROUTE or any(
        marker in normalized for marker in TOOL_INTENT_MARKERS
    )
    request = None
    planner_mode = "skipped"
    if should_plan:
        try:
            request = plan_readonly_tool_with_llm(
                question,
                retrieved_docs=retrieved_docs,
                previous_citations=previous_citations,
                conversation_context=dict(state.get("answer_context") or {}),
            )
            if request is not None:
                planner_mode = "qwen_native"
        except RuntimeError:
            # 原生工具协议不可用时继续走可测试的本地规则，避免普通问答整体失败。
            planner_mode = "rule_fallback"
        if request is None:
            request = plan_readonly_tool(question, retrieved_docs)
            if request is not None:
                planner_mode = "rule_fallback"
    updated_state = dict(state)
    if request is None:
        updated_state["tool_call"] = {}
        updated_state["tool_used"] = False
    else:
        updated_state["tool_call"] = _model_dump(request)
        updated_state["tool_used"] = True
    updated_state["tool_planner_mode"] = planner_mode
    updated_state["node_trace"] = append_trace(
        state.get("node_trace"),
        [TOOL_DECISION_NODE],
    )
    return updated_state


def tool_call_node(state: GraphState, session: Session) -> GraphState:
    """执行一个有上限的只读工具调用，并把结果写入 Context Manager 输入。"""

    raw_call = dict(state.get("tool_call") or {})
    updated_state = dict(state)
    if not raw_call:
        updated_state["tool_results"] = []
        updated_state["tool_citations"] = []
        updated_state["tool_error"] = ""
        if str(state.get("route") or "") == TOOL_ROUTE:
            updated_state["tool_error"] = "无法根据上一轮引用确定只读工具或目标资源。"
            updated_state["need_human_review"] = True
            updated_state["review_reason"] = updated_state["tool_error"]
        updated_state["node_trace"] = append_trace(
            state.get("node_trace"),
            [TOOL_CALL_NODE],
        )
        return updated_state

    try:
        tool_request = ToolCallRequest(**raw_call)
        organization_id = int(state.get("organization_id") or 0)
        knowledge_base_id = int(state.get("knowledge_base_id") or 0)
        if not organization_id and knowledge_base_id:
            statement = select(KnowledgeBase).where(KnowledgeBase.id == knowledge_base_id)
            knowledge_base = session.exec(statement).first()
            organization_id = int(knowledge_base.organization_id) if knowledge_base else 0
        context = ToolExecutionContext(
            organization_id=organization_id,
            knowledge_base_id=knowledge_base_id,
            user_id=int(state.get("user_id")) if state.get("user_id") else None,
            role=str(state.get("role") or "viewer"),
            conversation_id=int(state.get("conversation_id")) if state.get("conversation_id") else None,
            request_id=get_request_id(),
            trace_id=get_trace_id(),
        )
    except Exception as exc:
        updated_state["tool_results"] = []
        updated_state["tool_citations"] = []
        updated_state["tool_error"] = f"invalid tool call: {exc}"
        updated_state["node_trace"] = append_trace(
            state.get("node_trace"),
            [TOOL_CALL_NODE],
        )
        return updated_state

    call_count = int(state.get("tool_call_count") or 0)
    max_calls = max(1, get_settings().agent_tool_max_calls_per_turn)
    if call_count >= max_calls:
        result = ToolExecutionResult(
            tool_name=tool_request.name,
            ok=False,
            error_code="tool_call_limit",
            error_message="tool call limit reached for this turn",
        )
        record_tool_call_audit(
            session,
            request=tool_request,
            context=context,
            result=result,
            allowed=False,
            duration_seconds=0.0,
            reason="tool call limit reached",
        )
        updated_state["tool_call_count"] = call_count
    else:
        result = execute_readonly_tool(
            tool_request,
            context=context,
            session=session,
        )
        updated_state["tool_call_count"] = call_count + 1

    updated_state["tool_results"] = [result.model_dump() if hasattr(result, "model_dump") else result.dict()]
    updated_state["tool_citations"] = list(result.citations)
    updated_state["tool_error"] = str(result.error_message or "") if not result.ok else ""
    updated_state["node_trace"] = append_trace(
        state.get("node_trace"),
        [TOOL_CALL_NODE],
    )
    return updated_state


def answer_node(state: GraphState) -> GraphState:
    """Day 18 Answer Node。

    职责：
    - 基于 context / retrieved_docs 调用 Qwen 生成答案
    - 返回 answer
    - 返回 doc/chunk 级 citations
    """

    question = str(state.get("question") or "").strip()
    if not question:
        raise ValueError("question must not be empty")

    retrieved_docs = list(state.get("retrieved_docs") or [])
    has_answer_context = bool(state.get("answer_context"))
    answer_context = context_manager.build_answer_context(
        recent_context=dict(state.get("answer_context") or {}),
        retrieved_documents=retrieved_docs,
        tool_results=_tool_result_context_texts(state),
        relevant_history=list(state.get("relevant_history") or []),
        recovery_actions=list(state.get("context_recovery_actions") or []),
    )
    answer_context["rewrite_queries"] = list(state.get("rewrite_queries") or [])
    answer_context["retrieval_context"] = str(
        answer_context.get("retrieval_context") or ""
    )
    if has_answer_context:
        result = llm_answer_service.generate_answer(
            question,
            retrieved_docs,
            conversation_context=answer_context,
        )
    else:
        result = llm_answer_service.generate_answer(question, retrieved_docs)

    updated_state = dict(state)
    updated_state["answer"] = result.answer
    updated_state["context"] = result.context
    updated_state["answer_context"] = answer_context
    updated_state["citations"] = merge_citations(
        result.citations,
        list(state.get("tool_citations") or []),
    )
    updated_state["answer_used_fallback"] = result.used_fallback
    updated_state["node_trace"] = append_trace(state.get("node_trace"), [ANSWER_NODE, END_NODE])
    return updated_state


def _tool_result_context_texts(state: GraphState) -> list[str]:
    """只把工具协议中的 result 文本交给 Context Manager，避免传入任意对象。"""

    texts: list[str] = []
    all_results = list(state.get("history_tool_results") or []) + list(
        state.get("tool_results") or []
    )
    for raw_result in all_results:
        if not isinstance(raw_result, dict):
            continue
        try:
            tool_name = str(raw_result.get("tool_name") or "unknown")
            ok = bool(raw_result.get("ok"))
            data = raw_result.get("data") if isinstance(raw_result.get("data"), dict) else {}
            citations = raw_result.get("citations") if isinstance(raw_result.get("citations"), list) else []
            error_code = raw_result.get("error_code")
            error_message = raw_result.get("error_message")
            parts = [f"tool={tool_name}", f"ok={str(ok).lower()}", f"data={data}"]
            if citations:
                parts.append(f"citations={citations}")
            if error_code:
                parts.append(f"error_code={error_code}")
            if error_message:
                parts.append(f"error_message={error_message}")
            texts.append("; ".join(parts))
        except Exception:
            continue
    return texts


def merge_citations(*citation_groups: list[dict]) -> list[dict]:
    """按 doc/chunk/item 去重，同时保留工具来源字段。"""

    merged: list[dict] = []
    seen: set[tuple[object, object, object]] = set()
    for group in citation_groups:
        for citation in group:
            if not isinstance(citation, dict):
                continue
            key = (
                citation.get("doc_id"),
                citation.get("chunk_id"),
                citation.get("knowledge_item_id"),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(dict(citation))
    return merged


def _model_dump(model: object) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def relevance_check_node(state: GraphState) -> GraphState:
    """检查检索结果能否进入 Answer Node。

    职责：
    - 判断 docs 是否为空
    - 判断 BGE rerank score 是否过低
    - 检查金额、编号等关键实体是否命中
    - 决定 confident / need_review
    """

    retrieved_docs = list(state.get("retrieved_docs") or [])
    hit_count = int(state.get("retrieval_hit_count") or len(retrieved_docs))
    top_score = float(state.get("relevance_score") or 0.0)
    threshold = get_settings().retrieval_rerank_score_threshold
    missing_entities = find_missing_critical_entities(
        str(state.get("question") or ""),
        retrieved_docs,
    )

    updated_state = dict(state)

    tool_succeeded = any(
        isinstance(result, dict) and bool(result.get("ok"))
        for result in list(state.get("tool_results") or [])
    )

    if hit_count == 0 and not tool_succeeded:
        updated_state["need_human_review"] = True
        updated_state["relevance_decision"] = "need_review"
        updated_state["review_reason"] = "no retrieved documents"
    elif top_score < threshold and not tool_succeeded:
        updated_state["need_human_review"] = True
        updated_state["relevance_decision"] = "need_review"
        updated_state["review_reason"] = (
            f"rerank score {top_score:.4f} below threshold {threshold:.2f}"
        )
    elif missing_entities:
        updated_state["need_human_review"] = True
        updated_state["relevance_decision"] = "need_review"
        updated_state["review_reason"] = (
            "retrieved docs do not cover critical query entities: "
            + ", ".join(missing_entities[:3])
        )
    else:
        updated_state["need_human_review"] = False
        updated_state["relevance_decision"] = "confident"
        updated_state["review_reason"] = ""

    updated_state["node_trace"] = append_trace(
        state.get("node_trace"),
        [RELEVANCE_CHECK_NODE],
    )
    return updated_state


def review_required_node(state: GraphState) -> GraphState:
    """Day 19 临时 review 分支。

    Day 20 会把这里升级成真正的 interrupt / resume human_review_node。
    当前先明确：证据不足时，不进入 answer_node。
    """

    updated_state = dict(state)
    updated_state["answer"] = "当前检索结果不足以支持直接回答，需要人工复核。"
    updated_state["answer_used_fallback"] = False
    updated_state["node_trace"] = append_trace(
        state.get("node_trace"),
        [REVIEW_NODE, END_NODE],
    )
    return updated_state


def human_review_result_node(
    state: GraphState,
    *,
    approved: bool,
    human_note: str = "",
) -> GraphState:
    """把人工审核结果写回状态。

    这个函数不直接触发 interrupt。
    Day 20 的 LangGraph human_review_node 会在拿到 resume 值后调用它。
    """

    updated_state = dict(state)
    updated_state["human_approved"] = approved
    updated_state["human_note"] = human_note.strip()
    updated_state["need_human_review"] = False

    if approved:
        updated_state["relevance_decision"] = "approved_by_human"
        updated_state["review_reason"] = ""
    else:
        updated_state["relevance_decision"] = "rejected_by_human"
        updated_state["review_reason"] = human_note.strip() or str(
            state.get("review_reason") or "rejected by human reviewer"
        )

    return updated_state


def review_rejected_node(state: GraphState) -> GraphState:
    """人工审核拒绝后的结束节点。"""

    updated_state = dict(state)
    human_note = str(state.get("human_note") or "").strip()
    if human_note:
        updated_state["answer"] = f"人工复核未通过：{human_note}"
    else:
        updated_state["answer"] = "人工复核未通过，当前流程已停止。"
    updated_state["answer_used_fallback"] = False
    updated_state["node_trace"] = append_trace(
        state.get("node_trace"),
        [REVIEW_NODE, END_NODE],
    )
    return updated_state


def route_question(
    question: str,
    knowledge_base_id: Optional[int],
    previous_citations: Optional[list[dict]] = None,
) -> tuple[str, str]:
    """规则兜底路由。

    目标不是覆盖一切，而是在 LLM Router 不可用时仍然满足最小验收：
    - 你好 -> direct
    - 什么是 RAG -> direct
    - 公司制度怎么报销 -> rag
    - 总结这个知识库的重点 -> complex
    """

    normalized = normalize_question(question)
    if is_direct_question(normalized):
        return (DIRECT_ROUTE, "matched direct rule")

    if previous_citations and any(marker in normalized for marker in TOOL_INTENT_MARKERS):
        return (TOOL_ROUTE, "当前问题引用上一轮检索结果，直接调用只读工具")

    if any(marker in normalized for marker in ("有哪些文档", "文档列表", "所有文档", "文件列表")):
        if knowledge_base_id is not None:
            return (TOOL_ROUTE, "问题要求查询知识库文档列表")

    if is_complex_question(normalized):
        if knowledge_base_id is None:
            return (DIRECT_ROUTE, "complex question without knowledge base id")
        return (COMPLEX_ROUTE, "matched complex rule")

    if knowledge_base_id is None:
        return (DIRECT_ROUTE, "no knowledge base id provided")

    return (RAG_ROUTE, "knowledge base search required")


def find_missing_critical_entities(
    question: str,
    retrieved_docs: list[rag_service.RetrievedDocument],
) -> list[str]:
    """找出问题中出现、但候选证据没有覆盖的关键实体。

    普通中文词不参与硬门禁，避免同义表达被误拒。当前只保护数字/金额、编号、
    英文产品标识和用户明确加引号的名称；后续可以接入组织级实体词典。
    """

    entities = extract_critical_query_entities(question)
    if not entities:
        return []

    support_text = normalize_match_text(
        "\n".join(build_document_support_text(document) for document in retrieved_docs[:3])
    )
    return [entity for entity in entities if entity not in support_text]


def extract_critical_query_entities(question: str) -> list[str]:
    """提取需要精确核对的数字、编号和引号内名称。"""

    normalized_question = normalize_match_text(question)
    candidates: list[str] = []
    candidates.extend(
        re.findall(
            r"(?<![a-z0-9])(?:[a-z]+[-_]?[a-z0-9]*\d[a-z0-9_-]*)(?![a-z0-9])",
            normalized_question,
        )
    )
    candidates.extend(
        re.findall(
            r"(?<![a-z0-9_-])(?:\d+(?:\.\d+)?|[零一二三四五六七八九十百千万亿两]+)"
            r"(?:亿|千万|百万|万|千|百)?(?:元|万元|块|%|天|个月|人|条|次)?",
            normalized_question,
        )
    )
    candidates.extend(
        value.lower()
        for value in re.findall(r"[\"“‘「【]([^\"”’」】]+)[\"”’」】]", question)
    )

    unique_entities: list[str] = []
    for entity in candidates:
        normalized_entity = normalize_match_text(entity)
        if normalized_entity and normalized_entity not in unique_entities:
            unique_entities.append(normalized_entity)
    return unique_entities


def get_document_relevance_score(document: rag_service.RetrievedDocument) -> float:
    """返回 relevance gate 使用的可比较分数。

    RRF 的分数通常只有 0.01 左右，不能作为门禁分数。正常检索结果应携带
    已归一化的 rerank score；保留 document.score 兜底，方便单元测试和旧数据兼容。
    """

    metadata = document.metadata or {}
    value = metadata.get("rerank_score")
    if value is not None:
        try:
            return float(value)
        except (TypeError, ValueError):
            pass
    return document.score


def build_document_support_text(document: rag_service.RetrievedDocument) -> str:
    """把标题、正文、heading_path 合并成支持性判断用文本。"""

    heading_path = document.metadata.get("heading_path") or []
    heading_text = ""
    if isinstance(heading_path, list):
        heading_text = " ".join(str(item) for item in heading_path)

    return normalize_match_text(
        "\n".join(
            [
                document.title,
                heading_text,
                document.content,
            ]
        )
    )


def normalize_match_text(text: str) -> str:
    """归一化匹配文本，便于做轻量包含判断。"""

    return re.sub(r"\s+", "", text).lower()


def is_direct_question(normalized_question: str) -> bool:
    if not normalized_question:
        return True

    for pattern in DIRECT_GREETING_PATTERNS:
        if re.match(pattern, normalized_question):
            return True

    for pattern in DIRECT_CONCEPT_PATTERNS:
        if re.match(pattern, normalized_question):
            return True

    return False


def is_complex_question(normalized_question: str) -> bool:
    for pattern in COMPLEX_PATTERNS:
        if re.match(pattern, normalized_question):
            return True
    return False


def normalize_question(question: str) -> str:
    return re.sub(r"\s+", "", question.strip().lower())


def build_docs_preview(documents: list[rag_service.RetrievedDocument], *, max_items: int = 3) -> str:
    """把检索结果压成一个简短预览，方便后面调试 workflow。"""

    if not documents:
        return ""

    preview_lines = []
    for index, document in enumerate(documents[:max_items], start=1):
        content_preview = build_content_preview(document.content)
        preview_lines.append(
            (
                f"[{index}] {document.title} | chunk_id={document.chunk_id} "
                f"| score={document.score:.4f} | {content_preview}"
            )
        )
    return "\n".join(preview_lines)


def build_content_preview(content: str, *, max_length: int = 80) -> str:
    """把 chunk 内容压成适合日志和调试看的短预览。"""

    normalized = " ".join(content.split()).strip()
    if len(normalized) <= max_length:
        return normalized
    return normalized[: max_length - 3] + "..."


def append_trace(existing_trace: Optional[list[str]], nodes: list[str]) -> list[str]:
    trace = list(existing_trace or [])
    for node in nodes:
        if not trace or trace[-1] != node:
            trace.append(node)
    return trace
