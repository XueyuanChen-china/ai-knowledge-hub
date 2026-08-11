"""工具结果的上下文生命周期策略。

工具结果进入 Context Pack 前先做确定性的结构化投影；完整结果由数据库存档，
后续 Pack 可以淘汰旧结果，但仍可通过 result_ref 恢复。这里不默认调用 LLM，
避免每次只读工具调用都增加额外延迟和成本。
"""

import json
from datetime import datetime
from uuid import uuid4
from typing import Any, Iterable, Optional

from sqlmodel import Session, select

from app.agent_tools.schemas import ToolExecutionResult
from app.db.models import Conversation, ConversationToolResult
from app.services.context_budget import estimate_tokens
from app.services.context_types import ToolResultRef


def _clip(value: Any, max_chars: int = 480) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _source_ids(result: ToolExecutionResult) -> list[str]:
    values: list[str] = []
    for citation in result.citations:
        if not isinstance(citation, dict):
            continue
        for key in ("chunk_id", "doc_id", "knowledge_item_id"):
            value = citation.get(key)
            if value is not None:
                values.append(f"{key}:{value}")
    return list(dict.fromkeys(values))


def build_tool_summary(result: ToolExecutionResult) -> str:
    """用工具自身的结构生成短摘要，不调用大模型。"""

    if not result.ok:
        return _clip(result.error_message or result.error_code or "工具执行失败")

    data = result.data or {}
    if isinstance(data.get("content"), str):
        label = data.get("filename") or data.get("title") or result.tool_name
        return _clip(f"{label}: {data['content']}")
    if isinstance(data.get("chunks"), list):
        chunks = data["chunks"]
        snippets = []
        for item in chunks[:3]:
            if isinstance(item, dict):
                snippets.append(_clip(item.get("content") or item.get("title") or "", 180))
        return _clip(f"{result.tool_name} 返回 {len(chunks)} 个 chunk；" + "；".join(snippets))
    if isinstance(data.get("documents"), list):
        documents = data["documents"]
        names = [_clip(item.get("filename") or item.get("title") or "", 80)
                 for item in documents[:5] if isinstance(item, dict)]
        return _clip(f"{result.tool_name} 返回 {len(documents)} 个文档：" + "、".join(names))
    return _clip(json.dumps(data, ensure_ascii=False, separators=(",", ":")))


def build_tool_result_ref(
    result: ToolExecutionResult,
    *,
    result_ref: Optional[str] = None,
    created_at: Optional[str] = None,
) -> ToolResultRef:
    source_ids = _source_ids(result)
    return ToolResultRef(
        tool_name=result.tool_name,
        summary=build_tool_summary(result),
        source_ids=source_ids,
        result_ref=result_ref or f"tool-result-{uuid4().hex}",
        truncated=estimate_tokens(result.to_context_text()) > 800,
        error=str(result.error_message or "") if not result.ok else "",
        created_at=created_at or datetime.utcnow().isoformat(),
        importance="important" if result.citations else "normal",
    )


def persist_tool_result(
    *,
    result: ToolExecutionResult,
    organization_id: int,
    conversation_id: Optional[int],
    thread_id: str,
    session: Session,
) -> ToolResultRef:
    """将完整结果存档，并返回可进入 Context Pack 的引用。"""

    ref = build_tool_result_ref(result)
    if conversation_id is None:
        return ref
    # Graph 单元测试、旧 checkpoint 或直接调用工具时可能只有一个候选 ID，
    # 但没有对应业务会话。工具存档不能因此让主问答流程因外键失败。
    try:
        conversation = session.get(Conversation, conversation_id)
    except Exception:
        conversation = None
    if conversation is None:
        return ref
    payload = result.model_dump() if hasattr(result, "model_dump") else result.dict()
    record = ConversationToolResult(
        organization_id=organization_id,
        conversation_id=conversation_id,
        thread_id=thread_id,
        result_ref=ref.result_ref,
        tool_name=ref.tool_name,
        summary=ref.summary,
        source_ids_json=json.dumps(ref.source_ids, ensure_ascii=False),
        full_result_json=json.dumps(payload, ensure_ascii=False, default=str),
        importance=ref.importance,
    )
    session.add(record)
    session.commit()
    return ref


def mark_tool_results_used(
    result_refs: Iterable[str],
    *,
    session: Session,
    citation_used: bool = False,
) -> None:
    """回答完成后标记工具结果，后续压缩时优先淘汰已消费结果。"""

    refs = [str(value) for value in result_refs if str(value)]
    if not refs:
        return
    now = datetime.utcnow()
    records = session.exec(
        select(ConversationToolResult).where(
            ConversationToolResult.result_ref.in_(refs)
        )
    ).all()
    for record in records:
        record.used_in_answer = True
        record.citation_used = bool(record.citation_used or citation_used)
        record.last_used_at = now
        session.add(record)
    session.commit()


def dedupe_tool_refs(refs: Iterable[ToolResultRef]) -> list[ToolResultRef]:
    """按 result_ref 和来源去重，保留较新的引用。"""

    result: list[ToolResultRef] = []
    seen: set[str] = set()
    for ref in reversed(list(refs)):
        key = ref.result_ref or f"{ref.tool_name}:{','.join(ref.source_ids)}"
        if key in seen:
            continue
        seen.add(key)
        result.append(ref)
    return list(reversed(result))


def compact_tool_refs(
    refs: Iterable[ToolResultRef],
    *,
    max_items: int,
) -> tuple[list[ToolResultRef], list[ToolResultRef]]:
    """按工具结果生命周期淘汰旧结果，不删除持久化记录。"""

    unique = dedupe_tool_refs(refs)
    protected = [ref for ref in unique if ref.importance == "pinned" or ref.citation_used]
    candidates = [ref for ref in unique if ref not in protected]
    candidate_slots = max(0, max_items - len(protected))
    keep = protected + (candidates[-candidate_slots:] if candidate_slots else [])
    kept_keys = {ref.result_ref for ref in keep}
    removed = [ref for ref in unique if ref.result_ref not in kept_keys]
    return keep, removed
