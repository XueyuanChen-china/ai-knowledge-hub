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
    organization_id = state.get("organization_id")
    retrieve_kwargs = {"top_k": top_k}
    if organization_id is not None:
        retrieve_kwargs["organization_id"] = int(organization_id)
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

    if hit_count == 0:
        updated_state["need_human_review"] = True
        updated_state["relevance_decision"] = "need_review"
        updated_state["review_reason"] = "no retrieved documents"
    elif top_score < threshold:
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
