import json
import re
from datetime import datetime
from time import sleep
from typing import Any, Iterator, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from langgraph.types import Command
from sqlalchemy import delete
from sqlmodel import Session, select

from app.db.database import get_session
from app.db.models import Conversation, KnowledgeBase, Message, ReviewTask
from app.graph import nodes as graph_nodes
from app.graph.langgraph_workflow import (
    build_checkpointed_workflow,
    get_checkpoint_snapshot_with_graph,
    get_thread_config,
    invoke_graph,
    resume_graph,
)
from app.graph.state import GraphState
from app.schemas.chat import (
    ChatRequest,
    ChatResumeRequest,
    ChatRunResponse,
    ConversationMessageResponse,
    ConversationSummaryResponse,
    ConversationUpdateRequest,
)
from app.services import llm_answer_service
from app.services import context_manager
from app.services import memory_service
from app.services.tool_context_policy import mark_tool_results_used
from app.security.dependencies import Principal, require_permission
from app.security.policies import PERMISSION_CHAT
from app.security.resource_access import (
    can_review_conversation,
    ensure_conversation_access,
    get_conversation_or_404,
    get_knowledge_base_or_404,
)

router = APIRouter(prefix="/api", tags=["chat"])
chat_dependency = require_permission(PERMISSION_CHAT)


SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}
ANSWER_REPLAY_INTERVAL_SECONDS = 0.06
REFERENCE_REPLAY_INTERVAL_SECONDS = 0.04


def ensure_knowledge_base_exists(
    knowledge_base_id: int,
    principal: Principal,
    session: Session,
) -> None:
    get_knowledge_base_or_404(knowledge_base_id, principal, session)


def ensure_conversation_exists(
    conversation_id: int,
    principal: Principal,
    session: Session,
) -> Conversation:
    conversation = get_conversation_or_404(conversation_id, principal, session)
    ensure_conversation_access(conversation, principal)
    return conversation


def ensure_checkpoint_belongs_to_conversation(snapshot, conversation: Conversation) -> None:
    """校验持久化 checkpoint 没有和业务会话脱节。

    数据库中的 conversation 是访问控制主事实；checkpoint 只是工作流暂停位置。
    恢复前必须同时匹配 thread、组织和 conversation，不能只相信客户端传入的
    thread_id，也不能把另一条会话的 checkpoint 接到当前会话上。
    """

    state = dict(snapshot.values or {})
    if not state:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Graph checkpoint not found",
        )

    if (
        str(state.get("thread_id") or "") != conversation.thread_id
        or int(state.get("organization_id") or 0) != conversation.organization_id
        or int(state.get("conversation_id") or 0) != conversation.id
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Graph checkpoint not found",
        )


def ensure_thread_conversation(
    *,
    knowledge_base_id: int,
    question: str,
    thread_id: Optional[str],
    principal: Principal,
    session: Session,
) -> Conversation:
    """确保 thread_id 对应的会话存在。"""

    if thread_id:
        statement = select(Conversation).where(
            Conversation.thread_id == thread_id,
            Conversation.organization_id == principal.organization_id,
        )
        conversation = session.exec(statement).first()
        if conversation is not None:
            ensure_conversation_access(conversation, principal)
            if conversation.knowledge_base_id != knowledge_base_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="thread_id does not belong to this knowledge base",
                )
            conversation.updated_at = datetime.utcnow()
            session.add(conversation)
            session.commit()
            session.refresh(conversation)
            return conversation

    conversation = Conversation(
        organization_id=principal.organization_id,
        created_by_user_id=principal.user_id,
        knowledge_base_id=knowledge_base_id,
        title=question[:50] or "New Conversation",
        thread_id=thread_id or uuid4().hex,
    )
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation


def touch_conversation(conversation_id: int, session: Session) -> None:
    conversation = session.get(Conversation, conversation_id)
    if conversation is None:
        return

    conversation.updated_at = datetime.utcnow()
    session.add(conversation)
    session.commit()


def save_user_message(conversation_id: int, question: str, session: Session) -> Message:
    message = Message(
        conversation_id=conversation_id,
        role="user",
        content=question,
        metadata_json="",
    )
    session.add(message)
    session.commit()
    session.refresh(message)
    # 只有用户明确表达“记住/以后按/最终决定”等指令时才写长期记忆。
    memory_service.capture_explicit_memories(
        conversation_id=conversation_id,
        message=message,
        session=session,
    )
    touch_conversation(conversation_id, session)
    return message


def save_assistant_message(
    conversation_id: int,
    answer: str,
    citations: list[dict],
    session: Session,
    tool_result_refs: Optional[list[dict]] = None,
    relevant_history: Optional[list[dict]] = None,
) -> None:
    message = Message(
        conversation_id=conversation_id,
        role="assistant",
        content=answer,
        metadata_json=json.dumps({"citations": citations}, ensure_ascii=False),
    )
    session.add(message)
    session.commit()
    mark_tool_results_used(
        [
            str(item.get("result_ref"))
            for item in tool_result_refs or []
            if isinstance(item, dict) and item.get("result_ref")
        ],
        session=session,
        citation_used=bool(citations),
    )
    touch_conversation(conversation_id, session)
    # 摘要失败只保留滑动窗口，不阻塞答案已经完成的主流程。
    context_manager.maybe_update_conversation_summary(
        conversation_id,
        session,
        relevant_history=relevant_history,
    )


def build_message_preview(content: str, max_length: int = 80) -> str:
    normalized = " ".join(content.split())
    if len(normalized) <= max_length:
        return normalized
    return normalized[: max_length - 3] + "..."


def parse_message_citations(metadata_json: str) -> list[dict]:
    if not metadata_json.strip():
        return []

    try:
        metadata = json.loads(metadata_json)
    except json.JSONDecodeError:
        return []

    citations = metadata.get("citations")
    if isinstance(citations, list):
        return [citation for citation in citations if isinstance(citation, dict)]
    return []


def build_conversation_summary(
    conversation: Conversation,
    session: Session,
) -> ConversationSummaryResponse:
    statement = (
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.desc(), Message.id.desc())
    )
    messages = session.exec(statement).all()
    latest_message = messages[0] if messages else None

    return ConversationSummaryResponse(
        id=conversation.id or 0,
        knowledge_base_id=conversation.knowledge_base_id,
        title=conversation.title,
        thread_id=conversation.thread_id,
        is_pinned=conversation.is_pinned,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        message_count=len(messages),
        last_message_preview=build_message_preview(latest_message.content)
        if latest_message
        else "",
        last_message_role=latest_message.role if latest_message else "",
    )


def build_conversation_message_response(
    message: Message,
) -> ConversationMessageResponse:
    return ConversationMessageResponse(
        id=message.id or 0,
        conversation_id=message.conversation_id,
        role=message.role,
        content=message.content,
        citations=parse_message_citations(message.metadata_json),
        created_at=message.created_at,
    )


def build_graph_contexts(
    conversation_id: int,
    principal: Principal,
    session: Session,
) -> dict[str, Any]:
    """为 Router、Rewrite、Answer 构造不同范围的会话上下文。"""

    statement = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc(), Message.id.asc())
    )
    messages = session.exec(statement).all()

    previous_citations: list[dict[str, Any]] = []
    for message in reversed(messages):
        if message.role != "assistant":
            continue
        previous_citations = parse_message_citations(message.metadata_json)
        break

    conversation = session.get(Conversation, conversation_id)
    summary = conversation.context_summary if conversation is not None else ""
    persistent_memory = memory_service.list_active_memories(
        conversation_id=conversation_id,
        organization_id=principal.organization_id,
        user_id=principal.user_id,
        role=principal.role,
        session=session,
    )
    contexts = context_manager.build_conversation_contexts(
        messages=messages,
        summary=summary,
        summary_through_message_id=(
            conversation.context_summary_through_message_id
            if conversation is not None
            else None
        ),
        persistent_memory=[
            {
                "kind": memory.memory_type,
                "content": memory.content,
                "source_ids": [str(memory.source_message_id or memory.id or "")],
                "importance": memory.importance,
                "pinned": True,
                "metadata": {
                    "memory_id": memory.id,
                    "source_message_id": memory.source_message_id,
                },
            }
            for memory in persistent_memory
        ],
    )
    # Router 和 tool planner 需要知道上一轮引用了哪些资源，才能处理“展开刚才文档”
    # 这类 follow-up；这里只传 ID 和标题，不把上一轮完整正文重复塞进 prompt。
    contexts["previous_citations"] = previous_citations
    return contexts


def build_retrieved_doc_preview_items_from_documents(
    documents: list[Any],
) -> list[dict[str, Any]]:
    """把 RetrievedDocument 列表转换成前端可直接展示的结构化候选证据。"""

    preview_items: list[dict[str, Any]] = []
    for index, document in enumerate(documents, start=1):
        content = str(getattr(document, "content", "") or "")
        preview_items.append(
            {
                "index": index,
                "doc_id": getattr(document, "doc_id", None),
                "chunk_id": getattr(document, "chunk_id", None),
                "knowledge_item_id": getattr(document, "knowledge_item_id", None),
                "title": str(getattr(document, "title", "") or ""),
                "content": content,
                "content_preview": build_message_preview(content, max_length=260),
                "score": float(getattr(document, "score", 0.0) or 0.0),
                "metadata": dict(getattr(document, "metadata", {}) or {}),
            }
        )
    return preview_items


def build_retrieved_doc_preview_items_from_state(
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    return build_retrieved_doc_preview_items_from_documents(
        list(state.get("retrieved_docs") or [])
    )


def create_or_update_review_task(
    *,
    conversation_id: int,
    question: str,
    docs_preview: str,
    session: Session,
) -> ReviewTask:
    statement = (
        select(ReviewTask)
        .where(ReviewTask.conversation_id == conversation_id)
        .where(ReviewTask.status == "pending")
    )
    task = session.exec(statement).first()
    if task is None:
        task = ReviewTask(
            conversation_id=conversation_id,
            question=question,
            docs_preview=docs_preview,
            status="pending",
        )
    else:
        task.question = question
        task.docs_preview = docs_preview
        task.updated_at = datetime.utcnow()

    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def update_review_task_after_resume(
    *,
    conversation_id: int,
    approved: bool,
    human_note: str,
    session: Session,
) -> Optional[ReviewTask]:
    statement = (
        select(ReviewTask)
        .where(ReviewTask.conversation_id == conversation_id)
        .where(ReviewTask.status == "pending")
    )
    task = session.exec(statement).first()
    if task is None:
        return None

    task.status = "approved" if approved else "rejected"
    task.human_note = human_note
    task.updated_at = datetime.utcnow()
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def build_response_from_state(
    *,
    state: dict,
    thread_id: str,
    conversation_id: Optional[int],
    status_text: str,
    review_payload: Optional[dict] = None,
) -> ChatRunResponse:
    return ChatRunResponse(
        status=status_text,
        thread_id=thread_id,
        conversation_id=conversation_id,
        route=str(state.get("route") or ""),
        route_reason=str(state.get("route_reason") or ""),
        answer=str(state.get("answer") or ""),
        citations=list(state.get("citations") or []),
        need_human_review=bool(state.get("need_human_review") or False),
        review_reason=str(state.get("review_reason") or ""),
        review_payload=review_payload,
        docs_preview=str(state.get("docs_preview") or ""),
        retrieved_docs_preview_items=build_retrieved_doc_preview_items_from_state(state),
        relevance_decision=str(state.get("relevance_decision") or ""),
        retrieval_hit_count=int(state.get("retrieval_hit_count") or 0),
        answer_used_fallback=state.get("answer_used_fallback"),
        tool_used=bool(state.get("tool_used") or False),
        tool_results=list(state.get("tool_results") or []),
        tool_error=str(state.get("tool_error") or ""),
        tool_call_count=int(state.get("tool_call_count") or 0),
        tool_planner_mode=str(state.get("tool_planner_mode") or ""),
        context_gap=dict(state.get("context_gap") or {}),
        history_recovery_used=bool(state.get("history_recovery_used") or False),
        relevant_history=list(state.get("relevant_history") or []),
        node_trace=list(state.get("node_trace") or []),
    )


def encode_sse_event(event: str, data: Any) -> str:
    """把事件编码成标准 SSE 文本。"""

    if isinstance(data, str):
        data_lines = data.split("\n") or [""]
        encoded_data = "\n".join(f"data: {line}" for line in data_lines)
    else:
        encoded_data = (
            f"data: {json.dumps(jsonable_encoder(data), ensure_ascii=False)}"
        )

    return f"event: {event}\n{encoded_data}\n\n"


def build_node_progress_payload(
    node_name: str,
    state: dict[str, Any],
) -> dict[str, Any]:
    """把图节点更新压缩成前端更容易消费的进度事件。"""

    payload: dict[str, Any] = {
        "node": node_name,
        "node_trace": list(state.get("node_trace") or []),
    }

    if node_name == "router":
        payload["route"] = str(state.get("route") or "")
        payload["route_reason"] = str(state.get("route_reason") or "")
    elif node_name == "retrieve":
        payload["retrieval_hit_count"] = int(state.get("retrieval_hit_count") or 0)
        payload["docs_preview"] = str(state.get("docs_preview") or "")
        payload["retrieved_docs_preview_items"] = (
            build_retrieved_doc_preview_items_from_state(state)
        )
    elif node_name == "query_rewrite":
        payload["rewrite_decision"] = str(state.get("rewrite_decision") or "")
        payload["rewrite_reason"] = str(state.get("rewrite_reason") or "")
        payload["rewrite_queries"] = list(state.get("rewrite_queries") or [])
    elif node_name == "context_gap_check":
        payload["context_gap"] = dict(state.get("context_gap") or {})
    elif node_name == "history_recovery":
        payload["context_gap"] = dict(state.get("context_gap") or {})
        payload["history_recovery_used"] = bool(state.get("history_recovery_used") or False)
        payload["relevant_history"] = list(state.get("relevant_history") or [])
    elif node_name == "tool_decision":
        payload["tool_used"] = bool(state.get("tool_used") or False)
        payload["tool_planner_mode"] = str(state.get("tool_planner_mode") or "")
        payload["tool_call"] = dict(state.get("tool_call") or {})
    elif node_name == "tool_call":
        payload["tool_used"] = bool(state.get("tool_used") or False)
        payload["tool_results"] = list(state.get("tool_results") or [])
        payload["tool_error"] = str(state.get("tool_error") or "")
        payload["tool_call_count"] = int(state.get("tool_call_count") or 0)
    elif node_name == "relevance_check":
        payload["retrieval_hit_count"] = int(state.get("retrieval_hit_count") or 0)
        payload["retrieved_docs_preview_items"] = (
            build_retrieved_doc_preview_items_from_state(state)
        )
        payload["relevance_decision"] = str(state.get("relevance_decision") or "")
        payload["review_reason"] = str(state.get("review_reason") or "")
        payload["need_human_review"] = bool(state.get("need_human_review") or False)
    elif node_name in {"human_review", "review_rejected"}:
        payload["relevance_decision"] = str(state.get("relevance_decision") or "")
        payload["review_reason"] = str(state.get("review_reason") or "")
        payload["human_note"] = str(state.get("human_note") or "")
    elif node_name == "answer":
        payload["answer"] = str(state.get("answer") or "")
        payload["citations"] = list(state.get("citations") or [])
        payload["answer_used_fallback"] = state.get("answer_used_fallback")

    return payload


def build_stream_response_payload(
    *,
    state: dict[str, Any],
    thread_id: str,
    conversation_id: Optional[int],
    status_text: str,
    review_payload: Optional[dict] = None,
) -> dict[str, Any]:
    response = build_response_from_state(
        state=state,
        thread_id=thread_id,
        conversation_id=conversation_id,
        status_text=status_text,
        review_payload=review_payload,
    )
    return jsonable_encoder(response)


def build_answer_state_from_result(
    state: dict[str, Any],
    result,
) -> dict[str, Any]:
    """把流式答案最终结果合并回 GraphState 结构。"""

    updated_state = dict(state)
    updated_state["answer"] = result.answer
    updated_state["context"] = result.context
    updated_state["citations"] = graph_nodes.merge_citations(
        result.citations,
        list(state.get("tool_citations") or []),
    )
    updated_state["answer_used_fallback"] = result.used_fallback
    updated_state["node_trace"] = graph_nodes.append_trace(
        state.get("node_trace"),
        [graph_nodes.ANSWER_NODE, graph_nodes.END_NODE],
    )
    return updated_state


def split_answer_for_sse(answer: str) -> list[str]:
    """把完整答案拆成适合前端渐进展示的短句块。"""

    normalized = answer.strip()
    if not normalized:
        return []

    paragraphs = [
        paragraph.strip() for paragraph in normalized.split("\n\n") if paragraph.strip()
    ]
    chunks: list[str] = []

    for paragraph in paragraphs:
        sentence_parts = re.findall(r".+?(?:[。！？；\n]|$)", paragraph, flags=re.S)
        buffer = ""
        for part in sentence_parts:
            text = part.strip()
            if not text:
                continue

            if len(buffer) + len(text) <= 24:
                buffer += text
            else:
                if buffer:
                    chunks.append(buffer)
                buffer = text

        if buffer:
            chunks.append(buffer)

    return chunks or [normalized]


def strip_answer_reference_labels(answer: str) -> str:
    """移除答案末尾附带的参考来源标签。"""

    normalized = answer.strip()
    return re.sub(r"\n*\s*参考来源：(\[\d+\]\s*)+$", "", normalized).strip()


def build_reference_numbers_from_state(state: dict[str, Any]) -> list[int]:
    """把 citations 还原成当前上下文编号。"""

    retrieved_docs = list(state.get("retrieved_docs") or [])
    citations = list(state.get("citations") or [])
    reference_numbers: list[int] = []

    for citation in citations:
        doc_id = citation.get("doc_id")
        chunk_id = citation.get("chunk_id")
        for index, document in enumerate(retrieved_docs, start=1):
            if document.doc_id == doc_id and document.chunk_id == chunk_id:
                if index not in reference_numbers:
                    reference_numbers.append(index)
                break

    return reference_numbers


def replay_answer_events_from_state(
    *,
    state: dict[str, Any],
) -> tuple[dict[str, Any], list[tuple[str, Any, float]]]:
    """同步生成完整答案后，再按带节奏的 SSE 事件重放。"""

    question = str(state.get("question") or "").strip()
    documents = list(state.get("retrieved_docs") or [])
    answer_context = context_manager.build_answer_context(
        recent_context=dict(state.get("answer_context") or {}),
        retrieved_documents=documents,
        tool_results=[],
        tool_result_refs=list(state.get("tool_result_refs") or []),
        relevant_history=list(state.get("relevant_history") or []),
        recovery_actions=list(state.get("context_recovery_actions") or []),
    )
    result = llm_answer_service.generate_answer(
        question,
        documents,
        conversation_context=answer_context,
    )
    updated_state = build_answer_state_from_result(state, result)
    updated_state["answer_context"] = answer_context
    updated_state["answer"] = strip_answer_reference_labels(
        str(updated_state.get("answer") or "")
    )

    events: list[tuple[str, Any, float]] = [
        (
            "node",
            {
                "node": graph_nodes.ANSWER_NODE,
                "node_trace": graph_nodes.append_trace(
                    state.get("node_trace"),
                    [graph_nodes.ANSWER_NODE],
                ),
            },
            0.0,
        )
    ]

    for answer_chunk in split_answer_for_sse(str(updated_state.get("answer") or "")):
        events.append(("answer", answer_chunk, ANSWER_REPLAY_INTERVAL_SECONDS))

    events.append(
        (
            "references",
            build_reference_numbers_from_state(updated_state),
            REFERENCE_REPLAY_INTERVAL_SECONDS,
        )
    )
    events.append(
        (
            "completed",
            build_stream_response_payload(
                state=updated_state,
                thread_id=str(state.get("thread_id") or ""),
                conversation_id=state.get("conversation_id"),
                status_text="completed",
            ),
            0.0,
        )
    )
    return updated_state, events


def stream_chat_graph_events(
    payload: ChatRequest,
    principal: Principal,
    session: Session,
) -> Iterator[str]:
    """流式执行聊天图，按阶段推送 SSE 事件。"""

    ensure_knowledge_base_exists(payload.knowledge_base_id, principal, session)

    question = payload.question.strip()
    if not question:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="question must not be empty",
        )

    conversation = ensure_thread_conversation(
        knowledge_base_id=payload.knowledge_base_id,
        question=question,
        thread_id=payload.thread_id,
        principal=principal,
        session=session,
    )
    graph_contexts = build_graph_contexts(conversation.id, principal, session)
    save_user_message(conversation.id, question, session)

    initial_state: GraphState = {
        "question": question,
        "organization_id": principal.organization_id,
        "user_id": principal.user_id,
        "role": principal.role,
        "knowledge_base_id": payload.knowledge_base_id,
        "conversation_id": conversation.id,
        "thread_id": conversation.thread_id,
        **graph_contexts,
    }
    graph = build_checkpointed_workflow(session, retrieve_top_k=payload.retrieve_top_k)

    yield encode_sse_event(
        "start",
        {
            "thread_id": conversation.thread_id,
            "conversation_id": conversation.id,
            "knowledge_base_id": payload.knowledge_base_id,
            "question": question,
        },
    )

    try:
        for mode, chunk in graph.stream(
            initial_state,
            config=get_thread_config(conversation.thread_id),
            stream_mode=["updates"],
            interrupt_before=[graph_nodes.ANSWER_NODE],
        ):
            if mode != "updates" or not isinstance(chunk, dict):
                continue

            interrupt_payload = chunk.get("__interrupt__")
            if interrupt_payload:
                snapshot = get_checkpoint_snapshot_with_graph(graph, conversation.thread_id)
                state = dict(snapshot.values)
                if snapshot.interrupts and state.get("need_human_review"):
                    # human_review_node 的 interrupt() 才需要创建人工审核任务。
                    review_payload = dict(snapshot.interrupts[0].value)
                    review_task = create_or_update_review_task(
                        conversation_id=conversation.id,
                        question=question,
                        docs_preview=str(state.get("docs_preview") or ""),
                        session=session,
                    )
                    state["review_task_id"] = review_task.id
                    yield encode_sse_event(
                        "interrupted",
                        build_stream_response_payload(
                            state=state,
                            thread_id=conversation.thread_id,
                            conversation_id=conversation.id,
                            status_text="interrupted",
                            review_payload=review_payload,
                        ),
                    )
                    return

                # interrupt_before=["answer"] 是内部流式边界：图在 answer 前暂停，
                # 由当前函数切到 answer 的 SSE 重放，不应展示成人工审核中断。
                continue

            for node_name, node_state in chunk.items():
                if not isinstance(node_state, dict):
                    continue
                yield encode_sse_event(
                    "node",
                    build_node_progress_payload(node_name, node_state),
                )

        snapshot = get_checkpoint_snapshot_with_graph(graph, conversation.thread_id)
        state = dict(snapshot.values)

        if tuple(snapshot.next or ()) == (graph_nodes.ANSWER_NODE,):
            state, answer_events = replay_answer_events_from_state(state=state)
            for event_name, event_data, event_delay in answer_events:
                if event_delay > 0:
                    sleep(event_delay)
                yield encode_sse_event(event_name, event_data)
            save_assistant_message(
                conversation.id,
                str(state.get("answer") or ""),
                list(state.get("citations") or []),
                session,
                tool_result_refs=list(state.get("tool_result_refs") or []),
                relevant_history=list(state.get("relevant_history") or []),
            )
            return

        save_assistant_message(
            conversation.id,
            str(state.get("answer") or ""),
            list(state.get("citations") or []),
            session,
            tool_result_refs=list(state.get("tool_result_refs") or []),
            relevant_history=list(state.get("relevant_history") or []),
        )
        yield encode_sse_event(
            "completed",
            build_stream_response_payload(
                state=state,
                thread_id=conversation.thread_id,
                conversation_id=conversation.id,
                status_text="completed",
            ),
        )
    except HTTPException:
        raise
    except Exception as exc:
        yield encode_sse_event("error", {"detail": str(exc)})


def stream_resume_graph_events(
    payload: ChatResumeRequest,
    principal: Principal,
    session: Session,
) -> Iterator[str]:
    """流式恢复人工审核后的图执行。"""

    statement = select(Conversation).where(
        Conversation.thread_id == payload.thread_id,
        Conversation.organization_id == principal.organization_id,
    )
    conversation = session.exec(statement).first()
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation thread not found",
        )

    ensure_conversation_access(conversation, principal)
    graph = build_checkpointed_workflow(session, retrieve_top_k=payload.retrieve_top_k)
    snapshot = get_checkpoint_snapshot_with_graph(graph, payload.thread_id)
    ensure_checkpoint_belongs_to_conversation(snapshot, conversation)
    if not snapshot.interrupts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No pending interrupt for this thread",
        )

    yield encode_sse_event(
        "start",
        {
            "thread_id": payload.thread_id,
            "conversation_id": conversation.id,
            "approved": payload.approved,
        },
    )

    try:
        for mode, chunk in graph.stream(
            Command(
                resume={
                    "approved": payload.approved,
                    "human_note": payload.human_note,
                }
            ),
            config=get_thread_config(payload.thread_id),
            stream_mode=["updates"],
            interrupt_before=[graph_nodes.ANSWER_NODE],
        ):
            if mode != "updates" or not isinstance(chunk, dict):
                continue

            for node_name, node_state in chunk.items():
                if not isinstance(node_state, dict):
                    continue
                yield encode_sse_event(
                    "node",
                    build_node_progress_payload(node_name, node_state),
                )

        final_snapshot = get_checkpoint_snapshot_with_graph(graph, payload.thread_id)
        state = dict(final_snapshot.values)
        update_review_task_after_resume(
            conversation_id=conversation.id,
            approved=payload.approved,
            human_note=payload.human_note,
            session=session,
        )

        if tuple(final_snapshot.next or ()) == (graph_nodes.ANSWER_NODE,):
            state, answer_events = replay_answer_events_from_state(state=state)
            for event_name, event_data, event_delay in answer_events:
                if event_delay > 0:
                    sleep(event_delay)
                yield encode_sse_event(event_name, event_data)
            save_assistant_message(
                conversation.id,
                str(state.get("answer") or ""),
                list(state.get("citations") or []),
                session,
                tool_result_refs=list(state.get("tool_result_refs") or []),
                relevant_history=list(state.get("relevant_history") or []),
            )
            return

        save_assistant_message(
            conversation.id,
            str(state.get("answer") or ""),
            list(state.get("citations") or []),
            session,
            tool_result_refs=list(state.get("tool_result_refs") or []),
            relevant_history=list(state.get("relevant_history") or []),
        )
        yield encode_sse_event(
            "completed",
            build_stream_response_payload(
                state=state,
                thread_id=payload.thread_id,
                conversation_id=conversation.id,
                status_text="completed",
            ),
        )
    except HTTPException:
        raise
    except Exception as exc:
        yield encode_sse_event("error", {"detail": str(exc)})


def run_chat_graph_impl(
    payload: ChatRequest,
    principal: Principal,
    session: Session = Depends(get_session),
) -> ChatRunResponse:
    ensure_knowledge_base_exists(payload.knowledge_base_id, principal, session)

    question = payload.question.strip()
    if not question:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="question must not be empty",
        )

    conversation = ensure_thread_conversation(
        knowledge_base_id=payload.knowledge_base_id,
        question=question,
        thread_id=payload.thread_id,
        principal=principal,
        session=session,
    )
    graph_contexts = build_graph_contexts(conversation.id, principal, session)
    save_user_message(conversation.id, question, session)

    initial_state: GraphState = {
        "question": question,
        "organization_id": principal.organization_id,
        "user_id": principal.user_id,
        "role": principal.role,
        "knowledge_base_id": payload.knowledge_base_id,
        "conversation_id": conversation.id,
        "thread_id": conversation.thread_id,
        **graph_contexts,
    }
    graph = build_checkpointed_workflow(session, retrieve_top_k=payload.retrieve_top_k)
    invoke_graph(
        graph,
        state=initial_state,
        thread_id=conversation.thread_id,
    )

    snapshot = get_checkpoint_snapshot_with_graph(graph, conversation.thread_id)
    state = dict(snapshot.values)

    if snapshot.interrupts:
        review_payload = dict(snapshot.interrupts[0].value)
        review_task = create_or_update_review_task(
            conversation_id=conversation.id,
            question=question,
            docs_preview=str(state.get("docs_preview") or ""),
            session=session,
        )
        state["review_task_id"] = review_task.id
        return build_response_from_state(
            state=state,
            thread_id=conversation.thread_id,
            conversation_id=conversation.id,
            status_text="interrupted",
            review_payload=review_payload,
        )

    save_assistant_message(
        conversation.id,
        str(state.get("answer") or ""),
        list(state.get("citations") or []),
        session,
        tool_result_refs=list(state.get("tool_result_refs") or []),
        relevant_history=list(state.get("relevant_history") or []),
    )
    return build_response_from_state(
        state=state,
        thread_id=conversation.thread_id,
        conversation_id=conversation.id,
        status_text="completed",
    )


def resume_chat_graph_impl(
    payload: ChatResumeRequest,
    principal: Principal,
    session: Session = Depends(get_session),
) -> ChatRunResponse:
    statement = select(Conversation).where(
        Conversation.thread_id == payload.thread_id,
        Conversation.organization_id == principal.organization_id,
    )
    conversation = session.exec(statement).first()
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation thread not found",
        )

    ensure_conversation_access(conversation, principal)
    graph = build_checkpointed_workflow(session, retrieve_top_k=payload.retrieve_top_k)
    snapshot = get_checkpoint_snapshot_with_graph(graph, payload.thread_id)
    ensure_checkpoint_belongs_to_conversation(snapshot, conversation)
    if not snapshot.interrupts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No pending interrupt for this thread",
        )

    resume_graph(
        graph,
        thread_id=payload.thread_id,
        approved=payload.approved,
        human_note=payload.human_note,
    )

    final_snapshot = get_checkpoint_snapshot_with_graph(graph, payload.thread_id)
    state = dict(final_snapshot.values)

    update_review_task_after_resume(
        conversation_id=conversation.id,
        approved=payload.approved,
        human_note=payload.human_note,
        session=session,
    )
    save_assistant_message(
        conversation.id,
        str(state.get("answer") or ""),
        list(state.get("citations") or []),
        session,
        tool_result_refs=list(state.get("tool_result_refs") or []),
        relevant_history=list(state.get("relevant_history") or []),
    )

    return build_response_from_state(
        state=state,
        thread_id=payload.thread_id,
        conversation_id=conversation.id,
        status_text="completed",
    )


@router.get("/conversations", response_model=list[ConversationSummaryResponse])
def list_conversations(
    knowledge_base_id: Optional[int] = None,
    principal: Principal = Depends(chat_dependency),
    session: Session = Depends(get_session),
) -> list[ConversationSummaryResponse]:
    if knowledge_base_id is not None:
        ensure_knowledge_base_exists(knowledge_base_id, principal, session)

    statement = select(Conversation).where(
        Conversation.organization_id == principal.organization_id
    )
    if not can_review_conversation(principal):
        statement = statement.where(Conversation.created_by_user_id == principal.user_id)
    statement = statement.order_by(
        Conversation.is_pinned.desc(),
        Conversation.updated_at.desc(),
        Conversation.id.desc(),
    )
    if knowledge_base_id is not None:
        statement = statement.where(Conversation.knowledge_base_id == knowledge_base_id)

    conversations = session.exec(statement).all()
    return [
        build_conversation_summary(conversation, session)
        for conversation in conversations
        if conversation.id is not None
    ]


@router.patch(
    "/conversations/{conversation_id}",
    response_model=ConversationSummaryResponse,
)
def update_conversation(
    conversation_id: int,
    payload: ConversationUpdateRequest,
    principal: Principal = Depends(chat_dependency),
    session: Session = Depends(get_session),
) -> ConversationSummaryResponse:
    conversation = ensure_conversation_exists(conversation_id, principal, session)

    if payload.title is not None:
        title = payload.title.strip()
        if not title:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="conversation title must not be empty",
            )
        conversation.title = title[:200]

    if payload.is_pinned is not None:
        conversation.is_pinned = payload.is_pinned

    conversation.updated_at = datetime.utcnow()
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return build_conversation_summary(conversation, session)


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_conversation(
    conversation_id: int,
    principal: Principal = Depends(chat_dependency),
    session: Session = Depends(get_session),
) -> None:
    conversation = ensure_conversation_exists(conversation_id, principal, session)

    # 先显式删除子表记录，再删除会话本身，避免 PostgreSQL 外键约束失败。
    session.exec(delete(ReviewTask).where(ReviewTask.conversation_id == conversation.id))
    session.exec(delete(Message).where(Message.conversation_id == conversation.id))
    session.flush()

    session.delete(conversation)
    session.commit()


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=list[ConversationMessageResponse],
)
def list_conversation_messages(
    conversation_id: int,
    principal: Principal = Depends(chat_dependency),
    session: Session = Depends(get_session),
) -> list[ConversationMessageResponse]:
    conversation = ensure_conversation_exists(conversation_id, principal, session)

    statement = (
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.asc(), Message.id.asc())
    )
    messages = session.exec(statement).all()
    return [build_conversation_message_response(message) for message in messages]


@router.post("/chat", response_model=ChatRunResponse)
def run_chat_graph(
    payload: ChatRequest,
    principal: Principal = Depends(chat_dependency),
    session: Session = Depends(get_session),
) -> ChatRunResponse:
    return run_chat_graph_impl(payload, principal, session)


@router.post("/chat/stream")
def run_chat_graph_stream(
    payload: ChatRequest,
    principal: Principal = Depends(chat_dependency),
    session: Session = Depends(get_session),
) -> StreamingResponse:
    return StreamingResponse(
        stream_chat_graph_events(payload, principal, session),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.post("/review/resume", response_model=ChatRunResponse)
def resume_chat_graph(
    payload: ChatResumeRequest,
    principal: Principal = Depends(chat_dependency),
    session: Session = Depends(get_session),
) -> ChatRunResponse:
    return resume_chat_graph_impl(payload, principal, session)


@router.post("/review/resume/stream")
def resume_chat_graph_stream(
    payload: ChatResumeRequest,
    principal: Principal = Depends(chat_dependency),
    session: Session = Depends(get_session),
) -> StreamingResponse:
    return StreamingResponse(
        stream_resume_graph_events(payload, principal, session),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.post("/_legacy/chat", response_model=ChatRunResponse, include_in_schema=False)
def legacy_run_chat_graph(
    payload: ChatRequest,
    principal: Principal = Depends(chat_dependency),
    session: Session = Depends(get_session),
) -> ChatRunResponse:
    return run_chat_graph_impl(payload, principal, session)


@router.post(
    "/_legacy/chat/resume",
    response_model=ChatRunResponse,
    include_in_schema=False,
)
def legacy_resume_chat_graph(
    payload: ChatResumeRequest,
    principal: Principal = Depends(chat_dependency),
    session: Session = Depends(get_session),
) -> ChatRunResponse:
    return resume_chat_graph_impl(payload, principal, session)
