import re
from typing import Optional

from sqlmodel import Session

from app.config import get_settings
from app.graph.state import GraphState
from app.services import llm_answer_service, llm_router_service, rag_service

START_NODE = "START"
ROUTER_NODE = "router"
DIRECT_NODE = "direct"
RETRIEVE_NODE = "retrieve"
RELEVANCE_CHECK_NODE = "relevance_check"
REVIEW_NODE = "review"
ANSWER_NODE = "answer"
COMPLEX_NODE = "complex"
END_NODE = "END"

DIRECT_ROUTE = "direct"
RAG_ROUTE = "rag"
COMPLEX_ROUTE = "complex"

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

GENERIC_QUERY_TERMS = {
    "什么",
    "什么是",
    "多少",
    "标准",
    "条件",
    "要求",
    "流程",
    "规定",
    "制度",
    "内容",
    "问题",
    "公司",
    "这个",
    "那个",
    "一下",
    "如何",
    "怎么",
    "哪些",
    "是否",
    "需要",
    "可以",
    "一下子",
}


def router_node(state: GraphState) -> GraphState:
    """Day 16 Router：先尝试 LLM，再用规则兜底。"""

    question = str(state.get("question") or "").strip()
    if not question:
        raise ValueError("question must not be empty")

    knowledge_base_id = state.get("knowledge_base_id")
    llm_decision = llm_router_service.route_question_with_llm(
        question,
        knowledge_base_id,
    )
    if llm_decision is not None:
        route = llm_decision.route
        route_reason = llm_decision.reason
    else:
        route, route_reason = route_question(question, knowledge_base_id)

    updated_state = dict(state)
    updated_state["route"] = route
    updated_state["route_reason"] = route_reason
    updated_state["node_trace"] = append_trace(state.get("node_trace"), [START_NODE, ROUTER_NODE])
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

    retrieved_docs = rag_service.retrieve(
        question,
        int(knowledge_base_id),
        session,
        top_k=top_k,
    )
    context = rag_service.format_context(retrieved_docs)

    updated_state = dict(state)
    updated_state["retrieved_docs"] = retrieved_docs
    updated_state["retrieval_hit_count"] = len(retrieved_docs)
    updated_state["context"] = context
    updated_state["docs_preview"] = build_docs_preview(retrieved_docs)
    updated_state["citations"] = rag_service.build_citations(retrieved_docs)
    updated_state["relevance_score"] = (
        max((document.score for document in retrieved_docs), default=0.0)
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
    result = llm_answer_service.generate_answer(
        question,
        retrieved_docs,
    )

    updated_state = dict(state)
    updated_state["answer"] = result.answer
    updated_state["context"] = result.context
    updated_state["citations"] = result.citations
    updated_state["answer_used_fallback"] = result.used_fallback
    updated_state["node_trace"] = append_trace(state.get("node_trace"), [ANSWER_NODE, END_NODE])
    return updated_state


def relevance_check_node(state: GraphState) -> GraphState:
    """Day 19 Relevance Check Node。

    职责：
    - 判断 docs 是否为空
    - 判断 top score 是否过低
    - 决定 confident / need_review
    """

    retrieved_docs = list(state.get("retrieved_docs") or [])
    hit_count = int(state.get("retrieval_hit_count") or len(retrieved_docs))
    top_score = float(state.get("relevance_score") or 0.0)
    threshold = get_settings().relevance_low_score_threshold
    support = evaluate_retrieval_support(
        str(state.get("question") or ""),
        retrieved_docs,
    )

    updated_state = dict(state)

    if hit_count == 0:
        updated_state["need_human_review"] = True
        updated_state["relevance_decision"] = "need_review"
        updated_state["review_reason"] = "no retrieved documents"
    elif top_score < threshold:
        updated_state["need_human_review"] = True
        updated_state["relevance_decision"] = "need_review"
        updated_state["review_reason"] = (
            f"top score {top_score:.4f} below threshold {threshold:.2f}"
        )
    elif not support["is_supported"]:
        updated_state["need_human_review"] = True
        updated_state["relevance_decision"] = "need_review"
        if support["matched_terms"]:
            updated_state["review_reason"] = (
                "retrieved docs only weakly match key query terms: "
                + ", ".join(support["matched_terms"][:3])
            )
        else:
            updated_state["review_reason"] = (
                "retrieved docs do not cover key query terms"
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


def route_question(question: str, knowledge_base_id: Optional[int]) -> tuple[str, str]:
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

    if is_complex_question(normalized):
        if knowledge_base_id is None:
            return (DIRECT_ROUTE, "complex question without knowledge base id")
        return (COMPLEX_ROUTE, "matched complex rule")

    if knowledge_base_id is None:
        return (DIRECT_ROUTE, "no knowledge base id provided")

    return (RAG_ROUTE, "knowledge base search required")


def evaluate_retrieval_support(
    question: str,
    retrieved_docs: list[rag_service.RetrievedDocument],
) -> dict[str, object]:
    """检查检索结果是否真的覆盖了问题里的关键语义词。"""

    significant_terms = extract_significant_query_terms(question)
    if not significant_terms:
        return {
            "is_supported": True,
            "matched_terms": [],
        }

    matched_terms: list[str] = []
    for document in retrieved_docs[:3]:
        support_text = build_document_support_text(document)
        for term in significant_terms:
            if term in support_text and term not in matched_terms:
                matched_terms.append(term)

    has_long_match = any(len(term) >= 3 for term in matched_terms)
    short_match_count = sum(1 for term in matched_terms if len(term) == 2)
    is_supported = has_long_match or short_match_count >= 2

    return {
        "is_supported": is_supported,
        "matched_terms": matched_terms,
    }


def extract_significant_query_terms(question: str) -> list[str]:
    """从问题里抽取更适合做支持性判断的关键词。"""

    unique_terms: list[str] = []
    for raw_term in rag_service.extract_query_terms(question):
        term = normalize_match_text(raw_term)
        if len(term) < 2 or term in GENERIC_QUERY_TERMS:
            continue
        if term not in unique_terms:
            unique_terms.append(term)

    unique_terms.sort(key=len, reverse=True)
    long_terms = [term for term in unique_terms if len(term) >= 3][:8]
    short_terms = [term for term in unique_terms if len(term) == 2][:8]
    return long_terms + short_terms


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
