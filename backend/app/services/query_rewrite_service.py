"""查询改写服务。

第一版先用规则决定是否值得调用 LLM，再让 LLM 生成少量补充查询。
原始问题始终保留，改写失败时退回原始问题。
"""

import json
import re
import time
from dataclasses import dataclass
from typing import Optional

from app.observability.metrics import get_metrics
from app.services import llm_router_service
from app.services import context_manager


REFERENCE_TERMS = (
    "这个",
    "那个",
    "它",
    "他们",
    "上述",
    "前面提到",
    "刚才",
    "该流程",
    "该制度",
    "这个流程",
    "这个制度",
)

CONTEXT_DEPENDENT_PATTERNS = (
    r"^(那|然后|继续|还有|除此之外)",
    r"(多久|谁负责|怎么处理|可以吗|是否一样|需要什么)(呢|吗)?[？?]?$",
)


@dataclass(frozen=True)
class RewriteDecision:
    """规则门控的结果。"""

    need_rewrite: bool
    reason: str
    confidence: float


def decide_query_rewrite(
    question: str,
    recent_messages: Optional[list[dict[str, str]]] = None,
) -> RewriteDecision:
    """判断问题是否依赖上下文。

    规则只负责判断“是否值得改写”，不负责生成改写文本。
    没有历史消息时，即使问题含有指代词，也不调用 LLM，避免生成无依据内容。
    """

    normalized = re.sub(r"\s+", "", question.strip())
    history = recent_messages or []
    if not normalized:
        return RewriteDecision(False, "empty_question", 1.0)
    if not history:
        return RewriteDecision(False, "no_conversation_context", 1.0)

    if any(term in normalized for term in REFERENCE_TERMS):
        return RewriteDecision(True, "contains_context_reference", 0.98)

    if len(normalized) <= 8:
        return RewriteDecision(True, "question_is_too_short", 0.9)

    if any(re.search(pattern, normalized) for pattern in CONTEXT_DEPENDENT_PATTERNS):
        return RewriteDecision(True, "matches_context_dependent_pattern", 0.86)

    return RewriteDecision(False, "question_is_self_contained", 0.92)


def rewrite_question_with_llm(
    question: str,
    recent_messages: list[dict[str, str]],
    conversation_context: Optional[dict] = None,
) -> Optional[list[str]]:
    """调用 Router 共用的 OpenAI 兼容模型生成 1~3 个补充查询。"""

    if not llm_router_service.is_llm_router_configured():
        return None

    settings = llm_router_service.get_settings()
    started_at = time.perf_counter()
    try:
        raw_output = llm_router_service.call_openai_compatible_chat(
            base_url=settings.llm_router_base_url,
            api_key=settings.llm_router_api_key,
            model=settings.llm_router_model,
            messages=build_rewrite_messages(
                question,
                recent_messages,
                conversation_context=conversation_context,
            ),
            timeout_seconds=settings.llm_router_timeout_seconds,
            reasoning_effort=settings.llm_router_reasoning_effort,
        )
        queries = parse_rewrite_output(raw_output, question)
    except RuntimeError:
        get_metrics().record_operation(
            "llm_query_rewrite", time.perf_counter() - started_at, outcome="error"
        )
        return None

    get_metrics().record_operation(
        "llm_query_rewrite", time.perf_counter() - started_at, outcome="success"
    )
    return queries


def build_rewrite_messages(
    question: str,
    recent_messages: list[dict[str, str]],
    conversation_context: Optional[dict] = None,
) -> list[dict[str, str]]:
    """只构造 Query Rewrite 所需的上下文，不传入检索结果和答案。"""

    raw_context = conversation_context or {}
    context_pack = context_manager.build_context_pack(
        purpose="rewrite",
        messages=recent_messages,
        summary=raw_context.get("conversation_summary")
        or str(raw_context.get("summary") or ""),
        current_question=question,
        system_instructions=list(raw_context.get("system_instructions") or []),
        persistent_memory=list(raw_context.get("persistent_memory") or []),
        relevant_history=list(raw_context.get("relevant_history") or []),
    )
    system_prompt = "\n".join(
        [
            "你是企业知识库的查询改写器。",
            "根据最近对话补全当前问题中的指代，但不能添加原对话没有的事实。",
            "保留金额、数字、制度编号、产品名和专有名词。",
            "生成 1 到 3 个适合 BM25 和向量检索的查询。",
            "只输出 JSON，不要输出额外解释。",
            '{"queries":["查询1","查询2"]}',
        ]
    )
    if context_pack.system_instructions:
        system_prompt += "\n本次请求的附加约束：\n" + "\n".join(
            context_pack.system_instructions
        )
    prompt_parts = [f"当前问题: {question.strip()}"]
    if context_pack.summary:
        prompt_parts.append(f"会话摘要:\n{context_pack.summary}")
    if context_pack.persistent_memory:
        prompt_parts.append(
            "长期记忆:\n"
            + "\n".join(item.content for item in context_pack.persistent_memory)
        )
    if context_pack.relevant_history:
        prompt_parts.append(
            "相关历史:\n"
            + "\n".join(item.content for item in context_pack.relevant_history)
        )
    prompt_parts.append(
        "最近对话:\n"
        + "\n".join(
            f"{message['role']}: {message['content']}"
            for message in context_pack.recent_messages
        )
    )
    user_prompt = "\n\n".join(prompt_parts)
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def parse_rewrite_output(raw_output: str, original_question: str) -> Optional[list[str]]:
    """解析并约束 LLM 的查询列表。"""

    normalized_output = llm_router_service.strip_markdown_code_fence(raw_output)
    try:
        payload = json.loads(normalized_output)
    except json.JSONDecodeError:
        return None

    raw_queries = payload.get("queries") if isinstance(payload, dict) else None
    if not isinstance(raw_queries, list):
        return None

    queries: list[str] = [original_question.strip()]
    for raw_query in raw_queries[:3]:
        query = re.sub(r"\s+", " ", str(raw_query or "").strip())
        if query and query not in queries and len(query) <= 300:
            queries.append(query)
    return queries if len(queries) > 1 else None
