"""跨业务资源的组织隔离与访问控制辅助函数。"""

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.db.models import Conversation, Document, KnowledgeBase, KnowledgeItem, UploadTask
from app.security.dependencies import Principal
from app.security.policies import ROLE_ADMIN, ROLE_OWNER


def resource_not_found(resource_name: str) -> HTTPException:
    """统一返回 404，避免跨组织请求通过 ID 枚举资源是否存在。"""

    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"{resource_name} not found",
    )


def get_knowledge_base_or_404(
    knowledge_base_id: int,
    principal: Principal,
    session: Session,
) -> KnowledgeBase:
    knowledge_base = session.exec(
        select(KnowledgeBase).where(
            KnowledgeBase.id == knowledge_base_id,
            KnowledgeBase.organization_id == principal.organization_id,
        )
    ).first()
    if knowledge_base is None:
        raise resource_not_found("Knowledge base")
    return knowledge_base


def get_document_or_404(
    document_id: int,
    principal: Principal,
    session: Session,
) -> Document:
    document = session.exec(
        select(Document).where(
            Document.id == document_id,
            Document.organization_id == principal.organization_id,
        )
    ).first()
    if document is None:
        raise resource_not_found("Document")
    return document


def get_knowledge_item_or_404(
    knowledge_item_id: int,
    principal: Principal,
    session: Session,
) -> KnowledgeItem:
    item = session.exec(
        select(KnowledgeItem).where(
            KnowledgeItem.id == knowledge_item_id,
            KnowledgeItem.organization_id == principal.organization_id,
        )
    ).first()
    if item is None:
        raise resource_not_found("Knowledge item")
    return item


def get_upload_task_or_404(
    upload_id: str,
    principal: Principal,
    session: Session,
) -> UploadTask:
    upload_task = session.exec(
        select(UploadTask).where(
            UploadTask.upload_id == upload_id,
            UploadTask.organization_id == principal.organization_id,
        )
    ).first()
    if upload_task is None:
        raise resource_not_found("Upload task")
    return upload_task


def get_conversation_or_404(
    conversation_id: int,
    principal: Principal,
    session: Session,
) -> Conversation:
    conversation = session.exec(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.organization_id == principal.organization_id,
        )
    ).first()
    if conversation is None:
        raise resource_not_found("Conversation")
    return conversation


def can_review_conversation(principal: Principal) -> bool:
    """当前第一版把 owner/admin 视为具备组织审核职责的成员。"""

    return principal.role in {ROLE_OWNER, ROLE_ADMIN}


def ensure_conversation_access(conversation: Conversation, principal: Principal) -> None:
    """会话仅归属用户本人或组织审核者可读取、恢复和修改。"""

    if conversation.created_by_user_id == principal.user_id:
        return
    if can_review_conversation(principal):
        return
    raise resource_not_found("Conversation")
