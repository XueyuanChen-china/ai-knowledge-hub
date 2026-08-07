"""当前会话范围内的只读历史工具。"""

import re
from datetime import datetime
from typing import Any, Dict, List, Tuple

from sqlmodel import Session, select

from app.agent_tools.knowledge_tools import KnowledgeToolError
from app.agent_tools.schemas import SearchConversationHistoryArgs, ToolExecutionContext
from app.db.models import Conversation, Message


def search_conversation_history(
    session: Session,
    context: ToolExecutionContext,
    arguments: SearchConversationHistoryArgs,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """在当前用户拥有的当前会话内找相关历史消息。"""

    if context.conversation_id is None or context.user_id is None:
        raise KnowledgeToolError(
            "conversation_scope_required",
            "conversation and user scope are required",
        )

    conversation = session.exec(
        select(Conversation).where(
            Conversation.id == context.conversation_id,
            Conversation.organization_id == context.organization_id,
            Conversation.knowledge_base_id == context.knowledge_base_id,
            Conversation.created_by_user_id == context.user_id,
        )
    ).first()
    if conversation is None:
        raise KnowledgeToolError("not_found", "conversation not found in current scope")

    # 第一版只读取有限历史，避免工具本身成为上下文放大器；后续可换成 PostgreSQL FTS。
    messages = session.exec(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.id.desc())
        .limit(200)
    ).all()
    scored = [_score_message(arguments.query, message) for message in messages]
    scored = [item for item in scored if item[0] > 0]
    scored.sort(key=lambda item: (-item[0], -int(item[1].id or 0)))

    items = []
    for score, message, matched_terms in scored[: arguments.limit]:
        items.append(
            {
                "message_id": message.id,
                "role": message.role,
                "content": str(message.content or "")[:4000],
                "created_at": message.created_at.isoformat() if message.created_at else "",
                "score": round(score, 4),
                "matched_terms": matched_terms,
            }
        )
    return (
        {
            "conversation_id": conversation.id,
            "query": arguments.query,
            "messages": items,
        },
        [],
    )


def _score_message(query: str, message: Message) -> Tuple[float, Message, List[str]]:
    content = _normalize(message.content)
    normalized_query = _normalize(query)
    if not content or not normalized_query:
        return 0.0, message, []

    terms = _terms(query)
    matched = [term for term in terms if term in content]
    score = 0.0
    if normalized_query in content:
        score += 5.0
    score += sum(1.0 + min(len(term), 8) / 8.0 for term in matched)

    # 最近消息在同等命中情况下略优，但不会压过明确的关键词命中。
    if message.created_at:
        age_days = max(0.0, (datetime.utcnow() - message.created_at).total_seconds() / 86400.0)
        score += 1.0 / (1.0 + age_days)
    return score, message, matched


def _terms(value: str) -> List[str]:
    normalized = _normalize(value)
    terms: List[str] = []
    terms.extend(token.lower() for token in re.findall(r"[a-z0-9_]+", normalized) if len(token) >= 2)
    for sequence in re.findall(r"[\u4e00-\u9fff]{2,}", normalized):
        terms.append(sequence)
        for size in (4, 3, 2):
            if len(sequence) >= size:
                terms.extend(sequence[index : index + size] for index in range(len(sequence) - size + 1))
    return list(dict.fromkeys(terms))


def _normalize(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()

