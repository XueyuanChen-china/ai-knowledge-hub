import json
from datetime import datetime
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from app.db.database import get_session
from app.db.models import Conversation, KnowledgeBase, Message, ReviewTask
from app.graph.langgraph_workflow import (
    build_checkpointed_workflow,
    get_checkpoint_snapshot,
    get_thread_config,
    invoke_graph,
    resume_graph,
)
from app.graph.state import GraphState
from app.schemas.chat import ChatRequest, ChatResumeRequest, ChatRunResponse

router = APIRouter(prefix="/api", tags=["chat"])


def ensure_knowledge_base_exists(knowledge_base_id: int, session: Session) -> None:
    knowledge_base = session.get(KnowledgeBase, knowledge_base_id)
    if knowledge_base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found",
        )


def ensure_thread_conversation(
    *,
    knowledge_base_id: int,
    question: str,
    thread_id: Optional[str],
    session: Session,
) -> Conversation:
    """确保 thread_id 对应的会话存在。"""

    if thread_id:
        statement = select(Conversation).where(Conversation.thread_id == thread_id)
        conversation = session.exec(statement).first()
        if conversation is not None:
            conversation.updated_at = datetime.utcnow()
            session.add(conversation)
            session.commit()
            session.refresh(conversation)
            return conversation

    conversation = Conversation(
        knowledge_base_id=knowledge_base_id,
        title=question[:50] or "New Conversation",
        thread_id=thread_id or uuid4().hex,
    )
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation


def save_user_message(conversation_id: int, question: str, session: Session) -> None:
    message = Message(
        conversation_id=conversation_id,
        role="user",
        content=question,
        metadata_json="",
    )
    session.add(message)
    session.commit()


def save_assistant_message(
    conversation_id: int,
    answer: str,
    citations: list[dict],
    session: Session,
) -> None:
    message = Message(
        conversation_id=conversation_id,
        role="assistant",
        content=answer,
        metadata_json=json.dumps({"citations": citations}, ensure_ascii=False),
    )
    session.add(message)
    session.commit()


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
        relevance_decision=str(state.get("relevance_decision") or ""),
        retrieval_hit_count=int(state.get("retrieval_hit_count") or 0),
        answer_used_fallback=state.get("answer_used_fallback"),
        node_trace=list(state.get("node_trace") or []),
    )


def run_chat_graph_impl(
    payload: ChatRequest,
    session: Session = Depends(get_session),
) -> ChatRunResponse:
    ensure_knowledge_base_exists(payload.knowledge_base_id, session)

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
        session=session,
    )
    save_user_message(conversation.id, question, session)

    initial_state: GraphState = {
        "question": question,
        "knowledge_base_id": payload.knowledge_base_id,
        "conversation_id": conversation.id,
        "thread_id": conversation.thread_id,
    }
    invoke_graph(
        session,
        state=initial_state,
        thread_id=conversation.thread_id,
        retrieve_top_k=payload.retrieve_top_k,
    )

    graph = build_checkpointed_workflow(session, retrieve_top_k=payload.retrieve_top_k)
    snapshot = graph.get_state(get_thread_config(conversation.thread_id))
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
    )
    return build_response_from_state(
        state=state,
        thread_id=conversation.thread_id,
        conversation_id=conversation.id,
        status_text="completed",
    )


def resume_chat_graph_impl(
    payload: ChatResumeRequest,
    session: Session = Depends(get_session),
) -> ChatRunResponse:
    statement = select(Conversation).where(Conversation.thread_id == payload.thread_id)
    conversation = session.exec(statement).first()
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation thread not found",
        )

    graph = build_checkpointed_workflow(session, retrieve_top_k=payload.retrieve_top_k)
    snapshot = graph.get_state(get_thread_config(payload.thread_id))
    if not snapshot.interrupts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No pending interrupt for this thread",
        )

    resume_graph(
        session,
        thread_id=payload.thread_id,
        approved=payload.approved,
        human_note=payload.human_note,
        retrieve_top_k=payload.retrieve_top_k,
    )

    final_snapshot = graph.get_state(get_thread_config(payload.thread_id))
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
    )

    return build_response_from_state(
        state=state,
        thread_id=payload.thread_id,
        conversation_id=conversation.id,
        status_text="completed",
    )


@router.post("/chat", response_model=ChatRunResponse)
def run_chat_graph(
    payload: ChatRequest,
    session: Session = Depends(get_session),
) -> ChatRunResponse:
    return run_chat_graph_impl(payload, session)


@router.post("/review/resume", response_model=ChatRunResponse)
def resume_chat_graph(
    payload: ChatResumeRequest,
    session: Session = Depends(get_session),
) -> ChatRunResponse:
    return resume_chat_graph_impl(payload, session)


@router.post("/_legacy/chat", response_model=ChatRunResponse, include_in_schema=False)
def legacy_run_chat_graph(
    payload: ChatRequest,
    session: Session = Depends(get_session),
) -> ChatRunResponse:
    return run_chat_graph_impl(payload, session)


@router.post(
    "/_legacy/chat/resume",
    response_model=ChatRunResponse,
    include_in_schema=False,
)
def legacy_resume_chat_graph(
    payload: ChatResumeRequest,
    session: Session = Depends(get_session),
) -> ChatRunResponse:
    return resume_chat_graph_impl(payload, session)
