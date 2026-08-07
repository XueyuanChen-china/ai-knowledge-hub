"""会话级长期记忆服务。

第一版只接受用户明确表达的记忆指令，不让模型自动把普通聊天写成长期记忆。
这样可以降低误记忆和事实污染风险，并保留后续增加人工确认的空间。
"""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlmodel import Session, select

from app.db.models import Conversation, ConversationMemory, Message


MEMORY_PATTERNS = (
    ("constraint", re.compile(r"^(?:请)?记住[：:]?\s*(.+)$")),
    ("constraint", re.compile(r"^以后(?:都|请)?按[：:]?\s*(.+)$")),
    ("decision", re.compile(r"^(?:最终决定|确定采用|确定使用)[：:]?\s*(.+)$")),
    ("constraint", re.compile(r"^不要再改[：:]?\s*(.+)$")),
)


@dataclass(frozen=True)
class MemoryCandidate:
    memory_type: str
    content: str
    importance: float = 1.0


def extract_explicit_memory_candidates(content: str) -> list[MemoryCandidate]:
    """从用户消息中提取明确的“请记住”类指令。"""

    normalized = " ".join(str(content or "").strip().split())
    if not normalized:
        return []

    for memory_type, pattern in MEMORY_PATTERNS:
        match = pattern.match(normalized)
        if not match:
            continue
        value = match.group(1).strip(" \t\r\n。！？!?；;")
        if len(value) < 2:
            return []
        return [
            MemoryCandidate(
                memory_type=memory_type,
                content=value[:2000],
                importance=1.0,
            )
        ]
    return []


def capture_explicit_memories(
    *,
    conversation_id: int,
    message: Message,
    session: Session,
) -> list[ConversationMemory]:
    """保存当前用户消息中明确声明的记忆，并对完全重复内容去重。"""

    candidates = extract_explicit_memory_candidates(message.content)
    if not candidates:
        return []

    conversation = session.get(Conversation, conversation_id)
    if conversation is None or message.id is None:
        return []

    existing = session.exec(
        select(ConversationMemory).where(
            ConversationMemory.conversation_id == conversation_id,
            ConversationMemory.organization_id == conversation.organization_id,
            ConversationMemory.status == "active",
        )
    ).all()
    existing_content = {_normalize(item.content) for item in existing}

    saved: list[ConversationMemory] = []
    for candidate in candidates:
        if _normalize(candidate.content) in existing_content:
            continue
        memory = ConversationMemory(
            organization_id=conversation.organization_id,
            conversation_id=conversation.id,
            user_id=conversation.created_by_user_id,
            memory_type=candidate.memory_type,
            content=candidate.content,
            source_message_id=message.id,
            importance=candidate.importance,
            status="active",
        )
        session.add(memory)
        saved.append(memory)
        existing_content.add(_normalize(candidate.content))

    if saved:
        session.commit()
        for memory in saved:
            session.refresh(memory)
    return saved


def list_active_memories(
    *,
    conversation_id: int,
    organization_id: int,
    user_id: int,
    role: str,
    session: Session,
    limit: int = 20,
) -> list[ConversationMemory]:
    """按当前会话和组织读取长期记忆，并校验会话访问边界。"""

    conversation = session.exec(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.organization_id == organization_id,
        )
    ).first()
    if conversation is None:
        return []
    if conversation.created_by_user_id != user_id and role not in {"owner", "admin"}:
        return []

    statement = (
        select(ConversationMemory)
        .where(
            ConversationMemory.conversation_id == conversation_id,
            ConversationMemory.organization_id == organization_id,
            ConversationMemory.status == "active",
        )
        .order_by(
            ConversationMemory.importance.desc(),
            ConversationMemory.updated_at.desc(),
            ConversationMemory.id.desc(),
        )
        .limit(max(1, min(limit, 50)))
    )
    return list(session.exec(statement).all())


def archive_memory(
    *,
    memory_id: int,
    conversation_id: int,
    organization_id: int,
    user_id: int,
    role: str,
    session: Session,
) -> Optional[ConversationMemory]:
    """归档一条记忆；归档后不会再进入 ContextPack。"""

    memories = session.exec(
        select(ConversationMemory).where(
            ConversationMemory.id == memory_id,
            ConversationMemory.conversation_id == conversation_id,
            ConversationMemory.organization_id == organization_id,
        )
    ).first()
    if memories is None:
        return None
    conversation = session.get(Conversation, conversation_id)
    if conversation is None:
        return None
    if conversation.created_by_user_id != user_id and role not in {"owner", "admin"}:
        return None
    memories.status = "archived"
    memories.updated_at = datetime.utcnow()
    session.add(memories)
    session.commit()
    session.refresh(memories)
    return memories


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()
