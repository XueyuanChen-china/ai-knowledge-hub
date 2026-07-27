from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlmodel import Session, select

from app.db.database import get_session
from app.db.models import Document, KnowledgeBase, KnowledgeItem, UploadTask
from app.schemas.knowledge_base import (
    KnowledgeBaseCreate,
    KnowledgeBaseRead,
    KnowledgeBaseUpdate,
)
from app.security.dependencies import Principal, require_permission
from app.security.policies import (
    PERMISSION_KNOWLEDGE_BASE_DELETE,
    PERMISSION_KNOWLEDGE_BASE_READ,
    PERMISSION_KNOWLEDGE_BASE_WRITE,
)
from app.security.resource_access import get_knowledge_base_or_404

router = APIRouter(prefix="/knowledge-bases", tags=["knowledge-bases"])
read_dependency = require_permission(PERMISSION_KNOWLEDGE_BASE_READ)
write_dependency = require_permission(PERMISSION_KNOWLEDGE_BASE_WRITE)
delete_dependency = require_permission(PERMISSION_KNOWLEDGE_BASE_DELETE)


@router.post(
    "",
    response_model=KnowledgeBaseRead,
    status_code=status.HTTP_201_CREATED,
)
def create_knowledge_base(
    payload: KnowledgeBaseCreate,
    principal: Principal = Depends(write_dependency),
    session: Session = Depends(get_session),
) -> KnowledgeBase:
    """创建知识库。

    对应接口：POST /knowledge-bases
    """

    knowledge_base = KnowledgeBase(
        organization_id=principal.organization_id,
        created_by_user_id=principal.user_id,
        name=payload.name,
        description=payload.description,
    )

    session.add(knowledge_base)
    session.commit()
    session.refresh(knowledge_base)

    return knowledge_base


@router.get("", response_model=list[KnowledgeBaseRead])
def list_knowledge_bases(
    principal: Principal = Depends(read_dependency),
    session: Session = Depends(get_session),
) -> list[KnowledgeBase]:
    """查询知识库列表。

    对应接口：GET /knowledge-bases
    """

    statement = (
        select(KnowledgeBase)
        .where(KnowledgeBase.organization_id == principal.organization_id)
        .order_by(KnowledgeBase.created_at.desc())
    )
    return list(session.exec(statement).all())


@router.get("/{knowledge_base_id}", response_model=KnowledgeBaseRead)
def get_knowledge_base(
    knowledge_base_id: int,
    principal: Principal = Depends(read_dependency),
    session: Session = Depends(get_session),
) -> KnowledgeBase:
    """查询单个知识库。

    对应接口：GET /knowledge-bases/{id}
    """

    return get_knowledge_base_or_404(knowledge_base_id, principal, session)


@router.put("/{knowledge_base_id}", response_model=KnowledgeBaseRead)
def update_knowledge_base(
    knowledge_base_id: int,
    payload: KnowledgeBaseUpdate,
    principal: Principal = Depends(write_dependency),
    session: Session = Depends(get_session),
) -> KnowledgeBase:
    """更新知识库。

    对应接口：PUT /knowledge-bases/{id}
    """

    knowledge_base = get_knowledge_base_or_404(knowledge_base_id, principal, session)

    knowledge_base.name = payload.name
    knowledge_base.description = payload.description
    knowledge_base.updated_at = datetime.utcnow()

    session.add(knowledge_base)
    session.commit()
    session.refresh(knowledge_base)

    return knowledge_base


@router.delete("/{knowledge_base_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_knowledge_base(
    knowledge_base_id: int,
    principal: Principal = Depends(delete_dependency),
    session: Session = Depends(get_session),
) -> None:
    """删除知识库。

    对应接口：DELETE /knowledge-bases/{id}
    """

    knowledge_base = get_knowledge_base_or_404(knowledge_base_id, principal, session)

    dependency_counts = {
        "documents": session.exec(
            select(func.count()).select_from(Document).where(
                Document.knowledge_base_id == knowledge_base.id
            )
        ).one(),
        "knowledge_items": session.exec(
            select(func.count()).select_from(KnowledgeItem).where(
                KnowledgeItem.knowledge_base_id == knowledge_base.id
            )
        ).one(),
        "upload_tasks": session.exec(
            select(func.count()).select_from(UploadTask).where(
                UploadTask.knowledge_base_id == knowledge_base.id
            )
        ).one(),
    }
    dependencies = [name for name, count in dependency_counts.items() if count]
    if dependencies:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "Knowledge base has dependent resources", "dependencies": dependencies},
        )

    session.delete(knowledge_base)
    session.commit()
