"""判断一次问答是否缺少会话上下文。

第一版只做可解释的确定性判断，不让 LLM 决定是否再次查询历史，避免每轮对话
都增加一次模型调用。真正的历史恢复由图节点按这个结果触发。
"""

import re
from dataclasses import dataclass, field
from typing import Any, List, Optional


REFERENCE_MARKERS = (
    "它",
    "这个",
    "那个",
    "上面",
    "之前",
    "刚才",
    "上次",
    "前面",
    "上述",
    "该问题",
    "该流程",
    "这个条件",
    "之前说过",
    "上次提到",
    "刚才说的",
)

EXPLICIT_HISTORY_MARKERS = (
    "之前说过",
    "上次提到",
    "刚才说的",
    "历史消息",
    "前面提到",
    "之前的对话",
)


@dataclass(frozen=True)
class ContextGapDecision:
    need_recovery: bool
    reason: str = ""
    missing_terms: List[str] = field(default_factory=list)
    triggers: List[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "need_recovery": self.need_recovery,
            "reason": self.reason,
            "missing_terms": list(self.missing_terms),
            "triggers": list(self.triggers),
        }


def detect_context_gap(question: str, context: Optional[dict[str, Any]]) -> ContextGapDecision:
    """根据问题和已有摘要/近期消息判断是否需要恢复历史。"""

    normalized_question = _normalize(question)
    if not normalized_question:
        return ContextGapDecision(False)

    context_text = _context_text(context or {})
    has_context = bool(_meaningful_text(context_text))
    triggers: list[str] = []
    missing_terms: list[str] = []

    if any(marker in normalized_question for marker in EXPLICIT_HISTORY_MARKERS):
        triggers.append("explicit_history_reference")

    pronouns = [
        marker
        for marker in REFERENCE_MARKERS
        if marker in normalized_question
        and marker not in EXPLICIT_HISTORY_MARKERS
    ]
    if pronouns:
        missing_terms.extend(pronouns)
        if not has_context:
            triggers.append("pronoun_without_antecedent")

    if len(normalized_question) <= 8 and not has_context:
        triggers.append("short_question_without_context")

    if not has_context and _looks_like_continuation(normalized_question):
        triggers.append("continuation_without_context")

    if not triggers:
        return ContextGapDecision(False)

    reason = "当前问题引用了历史对话，但已有摘要和近期消息不足以补全主体。"
    if "explicit_history_reference" in triggers:
        reason = "用户明确要求引用之前的对话，需要查询当前会话历史。"
    return ContextGapDecision(
        need_recovery=True,
        reason=reason,
        missing_terms=list(dict.fromkeys(missing_terms)),
        triggers=triggers,
    )


def _context_text(context: dict[str, Any]) -> str:
    parts: list[str] = []
    parts.append(str(context.get("summary") or ""))
    summary = context.get("conversation_summary")
    if isinstance(summary, dict):
        for key in ("facts", "decisions", "open_questions", "entities"):
            value = summary.get(key)
            if isinstance(value, list):
                parts.extend(str(item) for item in value)
    for message in context.get("recent_messages") or []:
        if isinstance(message, dict):
            parts.append(str(message.get("content") or ""))
        else:
            parts.append(str(message))
    for item in context.get("relevant_history") or []:
        if isinstance(item, dict):
            parts.append(str(item.get("content") or item.get("summary") or ""))
        else:
            parts.append(str(item))
    return "\n".join(parts)


def _meaningful_text(value: str) -> str:
    text = _normalize(value)
    for marker in REFERENCE_MARKERS:
        text = text.replace(marker, "")
    return re.sub(r"[\s，。！？、,:：;；]+", "", text)


def _looks_like_continuation(question: str) -> bool:
    return question.startswith(("那", "然后", "继续", "还要", "为什么", "怎么"))


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()
