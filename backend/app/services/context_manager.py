"""统一构造 Router、Rewrite、Answer 使用的结构化 Context Pack。

Context Management 只负责构造一次 LLM 请求的输入，不删除 messages 表中的历史。
它把摘要、近期消息、相关历史、检索证据和工具结果分成不同类型，再按预算选择。
"""

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, List, Optional, Tuple

from sqlmodel import Session, select

from app.config import get_settings
from app.db.models import Conversation, Message
from app.observability.metrics import get_metrics
from app.services.context_budget import ContextBudget, estimate_tokens
from app.services.context_types import (
    BudgetBreakdown,
    ContextItem,
    EvidenceItem,
    OmittedItem,
    RecoveryAction,
    StructuredSummary,
    ToolResultRef,
)


@dataclass(frozen=True)
class ContextPack:
    """发送给某个 LLM 节点的受控上下文。

    `summary`、`retrieval_context` 和 `tool_results` 保留旧接口兼容性；新增的
    结构化字段是后续节点和 checkpoint 使用的权威表示。
    """

    purpose: str
    recent_messages: List[dict[str, str]]
    summary: str
    retrieval_context: str
    tool_results: List[str]
    estimated_tokens: int
    truncated: bool
    system_instructions: List[str] = field(default_factory=list)
    pinned_constraints: List[ContextItem] = field(default_factory=list)
    persistent_memory: List[ContextItem] = field(default_factory=list)
    conversation_summary: Optional[StructuredSummary] = None
    relevant_history: List[ContextItem] = field(default_factory=list)
    evidence_items: List[EvidenceItem] = field(default_factory=list)
    tool_result_refs: List[ToolResultRef] = field(default_factory=list)
    budget: Optional[BudgetBreakdown] = None
    omitted_items: List[OmittedItem] = field(default_factory=list)
    recovery_actions: List[RecoveryAction] = field(default_factory=list)
    current_question: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转换成可写入 LangGraph checkpoint 的 JSON 兼容字典。"""

        return {
            "purpose": self.purpose,
            # 旧调用方仍从这些字段读取内容。
            "recent_messages": self.recent_messages,
            "summary": self.summary,
            "retrieval_context": self.retrieval_context,
            "tool_results": self.tool_results,
            "estimated_tokens": self.estimated_tokens,
            "truncated": self.truncated,
            # 新的结构化表示。
            "system_instructions": list(self.system_instructions),
            "pinned_constraints": [item.to_dict() for item in self.pinned_constraints],
            "persistent_memory": [item.to_dict() for item in self.persistent_memory],
            "conversation_summary": (
                self.conversation_summary.to_dict()
                if self.conversation_summary is not None
                else None
            ),
            "relevant_history": [item.to_dict() for item in self.relevant_history],
            "evidence_items": [item.to_dict() for item in self.evidence_items],
            "tool_result_refs": [item.to_dict() for item in self.tool_result_refs],
            "budget": self.budget.to_dict() if self.budget is not None else {},
            "omitted_items": [item.to_dict() for item in self.omitted_items],
            "recovery_actions": [item.to_dict() for item in self.recovery_actions],
            "current_question": self.current_question,
        }


def normalize_message(message: Any, max_chars: int) -> dict[str, str]:
    """把数据库消息或已有 context 消息归一化并限制长度。"""

    if isinstance(message, dict):
        role = str(message.get("role") or "user")
        content_value = message.get("content")
    else:
        role = str(getattr(message, "role", None) or "user")
        content_value = getattr(message, "content", None)
    content = " ".join(str(content_value or "").split())
    if max_chars > 0 and len(content) > max_chars:
        content = content[:max_chars].rstrip() + "...[已裁剪]"
    return {"role": role, "content": content}


def clip_text(text: str, max_tokens: int, suffix: str = "...[已裁剪]") -> tuple[str, bool]:
    """按估算 Token 预算裁剪文本，并返回是否发生裁剪。"""

    normalized = str(text or "").strip()
    if max_tokens <= 0:
        return "", bool(normalized)
    if estimate_tokens(normalized) <= max_tokens:
        return normalized, False

    max_chars = max(1, max_tokens * 2 - estimate_tokens(suffix) * 2)
    return normalized[:max_chars].rstrip() + suffix, True


def parse_structured_summary(value: Any) -> Optional[StructuredSummary]:
    """读取当前 JSON 摘要，同时兼容早期纯文本摘要。"""

    if isinstance(value, StructuredSummary):
        return value
    if isinstance(value, dict):
        return StructuredSummary.from_dict(value)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return StructuredSummary.from_legacy_text(text)
    return StructuredSummary.from_dict(parsed) or StructuredSummary.from_legacy_text(text)


def _coerce_context_items(raw_items: Any, kind: str) -> list[ContextItem]:
    items: list[ContextItem] = []
    for index, raw in enumerate(raw_items or []):
        if isinstance(raw, ContextItem):
            items.append(raw)
            continue
        if isinstance(raw, dict):
            content = str(raw.get("content") or raw.get("summary") or "").strip()
            if not content:
                continue
            source = raw.get("source_ids") or raw.get("source_id") or raw.get("id")
            source_ids = source if isinstance(source, list) else ([str(source)] if source else [])
            items.append(
                ContextItem(
                    kind=str(raw.get("kind") or kind),
                    content=content,
                    source_ids=[str(item) for item in source_ids],
                    importance=float(raw.get("importance") or raw.get("score") or 0.0),
                    pinned=bool(raw.get("pinned") or False),
                    metadata=dict(raw.get("metadata") or {}),
                )
            )
            continue
        text = str(raw or "").strip()
        if text:
            items.append(ContextItem(kind=kind, content=text, source_ids=[str(index)]))
    return items


def _format_evidence(item: EvidenceItem, index: Optional[int] = None) -> str:
    title = item.title.strip()
    location = []
    if item.document_id is not None:
        location.append(f"doc_id={item.document_id}")
    if item.chunk_id is not None:
        location.append(f"chunk_id={item.chunk_id}")
    prefix = " | ".join(part for part in (title, ", ".join(location)) if part)
    if index is not None:
        prefix = f"[{index}] {prefix}" if prefix else f"[{index}]"
    return f"{prefix}\n{item.content.strip()}" if prefix else item.content.strip()


def _select_atomic(
    items: Iterable[ContextItem],
    *,
    kind: str,
    remaining: int,
    omitted: list[OmittedItem],
) -> tuple[list[ContextItem], int, bool]:
    """按完整单元选择内容，放不下的单元整体省略。"""

    selected: list[ContextItem] = []
    truncated = False
    ordered = sorted(
        list(items),
        key=lambda item: (not item.pinned, -float(item.importance or 0.0)),
    )
    for item in ordered:
        token_count = estimate_tokens(item.content)
        if token_count <= remaining:
            selected.append(item)
            remaining -= token_count
        else:
            truncated = True
            omitted.append(
                OmittedItem(
                    kind=kind,
                    source_id=",".join(item.source_ids),
                    reason="budget_exhausted_or_atomic_item_too_large",
                    estimated_tokens=token_count,
                )
            )
    consumed = sum(estimate_tokens(item.content) for item in selected)
    return selected, consumed, truncated


def build_context_pack(
    *,
    purpose: str,
    messages: list[Any],
    summary: Any = "",
    retrieval_context: str = "",
    tool_results: Optional[list[str]] = None,
    current_question: str = "",
    system_instructions: Optional[list[str]] = None,
    pinned_constraints: Optional[list[Any]] = None,
    persistent_memory: Optional[list[Any]] = None,
    relevant_history: Optional[list[Any]] = None,
    evidence_items: Optional[list[EvidenceItem]] = None,
    tool_result_refs: Optional[list[ToolResultRef]] = None,
    recovery_actions: Optional[list[RecoveryAction]] = None,
) -> ContextPack:
    """按优先级构造结构化上下文。

    当前问题仍由调用方单独传给模型，因此不把问题重复塞进历史消息；它会保存在
    `current_question` 供观测和后续统一 prompt builder 使用。
    """

    budget = ContextBudget.for_purpose(purpose)
    omitted: list[OmittedItem] = []
    truncated = False
    remaining = budget.max_tokens
    breakdown = BudgetBreakdown(total=budget.max_tokens)

    # 系统指令和 pinned constraint 是最优先的，但当前节点通常由自己的 prompt 提供。
    selected_system: list[str] = []
    for instruction in system_instructions or []:
        clipped, was_truncated = clip_text(str(instruction), budget.max_system_tokens)
        if clipped and estimate_tokens(clipped) <= remaining:
            selected_system.append(clipped)
            tokens = estimate_tokens(clipped)
            remaining -= tokens
            breakdown.system_instructions += tokens
        truncated = truncated or was_truncated

    selected_pinned, pinned_tokens, pinned_truncated = _select_atomic(
        _coerce_context_items(pinned_constraints, "pinned_constraint"),
        kind="pinned_constraint",
        remaining=min(remaining, budget.max_pinned_tokens),
        omitted=omitted,
    )
    breakdown.pinned_constraints = pinned_tokens
    remaining -= breakdown.pinned_constraints
    truncated = truncated or pinned_truncated

    structured_summary = parse_structured_summary(summary)
    summary_text = structured_summary.to_prompt_text() if structured_summary else ""
    if not summary_text and summary:
        summary_text = str(summary).strip()
    summary_text, summary_truncated = clip_text(summary_text, min(remaining, budget.max_summary_tokens))
    breakdown.summary = estimate_tokens(summary_text)
    remaining -= breakdown.summary
    truncated = truncated or summary_truncated

    selected_memory, memory_tokens, memory_truncated = _select_atomic(
        _coerce_context_items(persistent_memory, "persistent_memory"),
        kind="persistent_memory",
        remaining=min(remaining, budget.max_persistent_memory_tokens),
        omitted=omitted,
    )
    breakdown.persistent_memory = memory_tokens
    remaining -= breakdown.persistent_memory
    truncated = truncated or memory_truncated

    selected_messages: list[dict[str, str]] = []
    # 从最新消息向前选取，最后再恢复时间顺序。
    for raw_message in reversed(messages):
        if len(selected_messages) >= budget.max_recent_messages or remaining <= 0:
            if raw_message:
                truncated = True
            break
        normalized = normalize_message(raw_message, budget.max_message_chars)
        message_tokens = estimate_tokens(normalized["content"])
        if message_tokens > remaining:
            truncated = True
            omitted.append(
                OmittedItem(
                    kind="recent_message",
                    source_id=str(getattr(raw_message, "id", None) or ""),
                    reason="message_does_not_fit_budget",
                    estimated_tokens=message_tokens,
                )
            )
            continue
        selected_messages.append(normalized)
        remaining -= message_tokens
        breakdown.recent_messages += message_tokens
    selected_messages.reverse()

    selected_history, history_tokens, history_truncated = _select_atomic(
        _coerce_context_items(relevant_history, "relevant_history"),
        kind="relevant_history",
        remaining=min(remaining, budget.max_history_tokens),
        omitted=omitted,
    )
    breakdown.relevant_history = history_tokens
    remaining -= breakdown.relevant_history
    truncated = truncated or history_truncated

    selected_evidence: list[EvidenceItem] = []
    if evidence_items:
        for item in evidence_items:
            rendered = _format_evidence(item)
            tokens = estimate_tokens(rendered)
            if tokens <= min(remaining, budget.max_retrieval_tokens):
                selected_evidence.append(item)
                remaining -= tokens
                breakdown.evidence += tokens
            else:
                truncated = True
                omitted.append(
                    OmittedItem(
                        kind="evidence",
                        source_id=item.source_id or str(item.chunk_id or item.document_id or ""),
                        reason="evidence_is_atomic_and_does_not_fit_budget",
                        estimated_tokens=tokens,
                    )
                )
        retrieval_text = "\n\n".join(
            _format_evidence(item, index=index)
            for index, item in enumerate(selected_evidence, start=1)
        )
    else:
        # 兼容旧调用：没有结构化证据时仍允许原始字符串被裁剪。
        retrieval_budget = min(remaining, budget.max_retrieval_tokens)
        retrieval_text, retrieval_truncated = clip_text(retrieval_context, retrieval_budget)
        breakdown.evidence = estimate_tokens(retrieval_text)
        remaining -= breakdown.evidence
        truncated = truncated or retrieval_truncated

    normalized_tools: list[str] = []
    selected_tool_refs: list[ToolResultRef] = []
    if tool_result_refs:
        tool_items = [
            ContextItem(
                kind="tool_result",
                content=(ref.summary or ref.content),
                source_ids=[ref.tool_name],
                importance=1.0,
            )
            for ref in tool_result_refs
        ]
        selected_tool_items, tool_item_tokens, tools_truncated = _select_atomic(
            tool_items,
            kind="tool_result",
            remaining=min(remaining, budget.max_tool_tokens),
            omitted=omitted,
        )
        selected_names = {item.source_ids[0] for item in selected_tool_items if item.source_ids}
        selected_tool_refs = [ref for ref in tool_result_refs if ref.tool_name in selected_names]
        normalized_tools = [ref.summary or ref.content for ref in selected_tool_refs]
        breakdown.tools = sum(estimate_tokens(item) for item in normalized_tools)
        remaining -= breakdown.tools
        truncated = truncated or tools_truncated
    else:
        tool_budget = min(remaining, budget.max_tool_tokens)
        for result in tool_results or []:
            if tool_budget <= 0:
                truncated = True
                break
            clipped_result, result_truncated = clip_text(str(result), tool_budget)
            if clipped_result:
                normalized_tools.append(clipped_result)
                result_tokens = estimate_tokens(clipped_result)
                tool_budget -= result_tokens
                remaining -= result_tokens
                breakdown.tools += result_tokens
            truncated = truncated or result_truncated

    used = budget.max_tokens - max(0, remaining)
    breakdown.used = used
    breakdown.remaining = max(0, remaining)
    estimated = (
        breakdown.system_instructions
        + breakdown.pinned_constraints
        + breakdown.summary
        + breakdown.recent_messages
        + breakdown.persistent_memory
        + breakdown.relevant_history
        + breakdown.evidence
        + breakdown.tools
    )
    get_metrics().record_context_pack(
        purpose,
        truncated=truncated,
        omitted_count=len(omitted),
    )
    return ContextPack(
        purpose=purpose,
        recent_messages=selected_messages,
        summary=summary_text,
        retrieval_context=retrieval_text,
        tool_results=normalized_tools,
        estimated_tokens=estimated,
        truncated=truncated,
        system_instructions=selected_system,
        pinned_constraints=selected_pinned,
        persistent_memory=selected_memory,
        conversation_summary=structured_summary,
        relevant_history=selected_history,
        evidence_items=selected_evidence,
        tool_result_refs=selected_tool_refs,
        budget=breakdown,
        omitted_items=omitted,
        recovery_actions=_coerce_recovery_actions(recovery_actions),
        current_question=str(current_question or ""),
    )


def build_conversation_contexts(
    *,
    messages: list[Message],
    summary: Any = "",
    persistent_memory: Optional[list[Any]] = None,
    relevant_history: Optional[list[Any]] = None,
    recovery_actions: Optional[list[RecoveryAction]] = None,
) -> dict[str, dict[str, Any]]:
    """为三个 LLM 节点生成隔离的 Context Pack。"""

    common = {
        "messages": messages,
        "summary": summary,
        "persistent_memory": persistent_memory,
        "relevant_history": relevant_history,
        "recovery_actions": recovery_actions,
    }
    router = build_context_pack(purpose="router", **common)
    rewrite = build_context_pack(purpose="rewrite", **common)
    answer = build_context_pack(purpose="answer", **common)
    return {
        "router_context": router.to_dict(),
        "rewrite_context": rewrite.to_dict(),
        "answer_context": answer.to_dict(),
    }


def build_answer_context(
    *,
    recent_context: Optional[dict[str, Any]],
    retrieved_documents: list[Any],
    tool_results: Optional[list[str]] = None,
    relevant_history: Optional[list[Any]] = None,
    recovery_actions: Optional[list[RecoveryAction]] = None,
) -> dict[str, Any]:
    """在检索完成后构造带证据预算的 Answer Context Pack。"""

    base = recent_context or {}
    evidence_items = [
        EvidenceItem(
            content=str(getattr(document, "content", "") or ""),
            source_id=str(getattr(document, "chunk_id", None) or getattr(document, "doc_id", None) or ""),
            document_id=getattr(document, "doc_id", None),
            chunk_id=getattr(document, "chunk_id", None),
            knowledge_item_id=getattr(document, "knowledge_item_id", None),
            title=str(getattr(document, "title", "") or ""),
            score=float(getattr(document, "score", 0.0) or 0.0),
            metadata=dict(getattr(document, "metadata", {}) or {}),
        )
        for document in retrieved_documents
    ]
    resolved_history = relevant_history if relevant_history is not None else base.get("relevant_history")
    resolved_tools = list(tool_results) if tool_results is not None else list(base.get("tool_results") or [])
    pack = build_context_pack(
        purpose="answer",
        messages=list(base.get("recent_messages") or []),
        summary=base.get("conversation_summary") or base.get("summary") or "",
        retrieval_context=str(base.get("retrieval_context") or ""),
        tool_results=resolved_tools,
        current_question=str(base.get("current_question") or ""),
        persistent_memory=list(base.get("persistent_memory") or []),
        relevant_history=resolved_history,
        evidence_items=evidence_items,
        recovery_actions=recovery_actions or _recovery_actions_from_dict(base.get("recovery_actions")),
    )
    return pack.to_dict()


def should_refresh_summary(messages: list[Message], conversation: Conversation) -> bool:
    """判断历史是否足够长，且是否出现了新的消息需要摘要。"""

    settings = get_settings()
    if not messages:
        return False
    latest_id = messages[-1].id
    cursor = conversation.context_summary_through_message_id
    if latest_id is None or latest_id == cursor:
        return False
    total_chars = sum(len(str(message.content or "")) for message in messages)
    if total_chars < settings.context_summary_trigger_chars:
        return False
    if cursor is None:
        return True
    new_chars = sum(
        len(str(message.content or ""))
        for message in messages
        if int(message.id or 0) > int(cursor)
    )
    return new_chars >= settings.context_summary_min_new_chars


def summarize_conversation_with_llm(
    messages: list[Message],
    previous_summary: Optional[StructuredSummary] = None,
) -> Optional[StructuredSummary]:
    """使用 Router 配置生成可追踪的结构化会话摘要。"""

    from app.services import llm_router_service

    if not llm_router_service.is_llm_router_configured():
        return None
    settings = get_settings()
    history_text = "\n".join(
        f"{message.id or ''} | {message.role}: {str(message.content or '').strip()}"
        for message in messages
    )
    previous_text = previous_summary.to_prompt_text() if previous_summary else "无"
    messages_payload = [
        {
            "role": "system",
            "content": (
                "你是会话摘要器。只总结输入对话中已经出现的事实、已确认决定、"
                "未解决问题、关键实体。禁止添加外部知识。只输出 JSON，结构固定为："
                '{"facts":[],"decisions":[],"open_questions":[],"entities":[]}'
            ),
        },
        {
            "role": "user",
            "content": f"已有摘要：\n{previous_text}\n\n新增对话：\n{history_text}",
        },
    ]
    started_at = time.perf_counter()
    try:
        raw_output = llm_router_service.call_openai_compatible_chat(
            base_url=settings.llm_router_base_url,
            api_key=settings.llm_router_api_key,
            model=settings.llm_router_model,
            messages=messages_payload,
            timeout_seconds=settings.llm_router_timeout_seconds,
            max_tokens=settings.context_summary_max_tokens,
            json_mode=True,
        )
    except Exception:
        get_metrics().record_operation(
            "llm_context_summary", time.perf_counter() - started_at, outcome="error"
        )
        return None

    try:
        payload = json.loads(llm_router_service.strip_markdown_code_fence(raw_output))
    except (TypeError, ValueError, json.JSONDecodeError):
        get_metrics().record_operation(
            "llm_context_summary", time.perf_counter() - started_at, outcome="invalid_json"
        )
        return None
    if isinstance(payload, dict) and isinstance(payload.get("summary"), dict):
        payload = payload["summary"]
    summary = StructuredSummary.from_dict(payload)
    if summary is None:
        get_metrics().record_operation(
            "llm_context_summary", time.perf_counter() - started_at, outcome="invalid_schema"
        )
        return None
    summary.generated_at = datetime.utcnow().isoformat()
    get_metrics().record_operation(
        "llm_context_summary", time.perf_counter() - started_at, outcome="success"
    )
    return summary


def maybe_update_conversation_summary(conversation_id: int, session: Session) -> bool:
    """超过阈值后尝试保存摘要；摘要失败不阻塞已经完成的答案。"""

    conversation = session.get(Conversation, conversation_id)
    if conversation is None:
        return False
    messages = session.exec(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc(), Message.id.asc())
    ).all()
    if not should_refresh_summary(messages, conversation):
        return False

    settings = get_settings()
    keep_recent = max(0, settings.context_summary_keep_recent_messages)
    summary_source = messages[:-keep_recent] if keep_recent else messages
    if not summary_source:
        return False
    cursor = conversation.context_summary_through_message_id or 0
    new_source = [message for message in summary_source if int(message.id or 0) > cursor]
    if not new_source:
        return False

    previous = parse_structured_summary(conversation.context_summary)
    summary = summarize_conversation_with_llm(new_source, previous)
    if summary is None:
        return False
    source_ids = list(previous.source_message_ids if previous else [])
    source_ids.extend(int(message.id) for message in new_source if message.id is not None)
    summary.source_message_ids = list(dict.fromkeys(source_ids))
    # 只记录实际进入摘要的最后一条消息；保留的 recent messages 不会被假装已摘要。
    summary.summarized_through_message_id = new_source[-1].id

    conversation.context_summary = json.dumps(summary.to_dict(), ensure_ascii=False)
    conversation.context_summary_version += 1
    conversation.context_summary_through_message_id = new_source[-1].id
    conversation.context_summary_updated_at = datetime.utcnow()
    session.add(conversation)
    session.commit()
    return True


def _recovery_actions_from_dict(value: Any) -> list[RecoveryAction]:
    actions: list[RecoveryAction] = []
    for raw in value or []:
        if not isinstance(raw, dict):
            continue
        actions.append(
            RecoveryAction(
                action=str(raw.get("action") or ""),
                reason=str(raw.get("reason") or ""),
                success=bool(raw.get("success") or False),
                source_ids=[str(item) for item in raw.get("source_ids") or []],
            )
        )
    return actions


def _coerce_recovery_actions(value: Any) -> list[RecoveryAction]:
    actions: list[RecoveryAction] = []
    for item in value or []:
        if isinstance(item, RecoveryAction):
            actions.append(item)
        elif isinstance(item, dict):
            actions.extend(_recovery_actions_from_dict([item]))
    return actions
