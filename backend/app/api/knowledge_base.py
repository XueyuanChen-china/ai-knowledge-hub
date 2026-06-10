from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from app.db.database import get_session
from app.db.models import KnowledgeBase
from app.schemas.knowledge_base import (
    KnowledgeBaseCreate,
    KnowledgeBaseRead,
    KnowledgeBaseUpdate,
)

router = APIRouter(prefix="/knowledge-bases", tags=["knowledge-bases"])


@router.post(
    "",
    response_model=KnowledgeBaseRead,
    status_code=status.HTTP_201_CREATED,
)
def create_knowledge_base(
    payload: KnowledgeBaseCreate,
    session: Session = Depends(get_session),
) -> KnowledgeBase:
    """创建知识库。

    对应接口：POST /knowledge-bases
    """

    knowledge_base = KnowledgeBase(
        name=payload.name,
        description=payload.description,
    )

    session.add(knowledge_base)
    session.commit()
    session.refresh(knowledge_base)

    return knowledge_base


@router.get("", response_model=list[KnowledgeBaseRead])
def list_knowledge_bases(
    session: Session = Depends(get_session),
) -> list[KnowledgeBase]:
    """查询知识库列表。

    对应接口：GET /knowledge-bases
    """

    statement = select(KnowledgeBase).order_by(KnowledgeBase.created_at.desc())
    return list(session.exec(statement).all())


@router.get("/{knowledge_base_id}", response_model=KnowledgeBaseRead)
def get_knowledge_base(
    knowledge_base_id: int,
    session: Session = Depends(get_session),
) -> KnowledgeBase:
    """查询单个知识库。

    对应接口：GET /knowledge-bases/{id}
    """

    knowledge_base = session.get(KnowledgeBase, knowledge_base_id)
    if knowledge_base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found",
        )

    return knowledge_base


@router.put("/{knowledge_base_id}", response_model=KnowledgeBaseRead)
def update_knowledge_base(
    knowledge_base_id: int,
    payload: KnowledgeBaseUpdate,
    session: Session = Depends(get_session),
) -> KnowledgeBase:
    """更新知识库。

    对应接口：PUT /knowledge-bases/{id}
    """

    knowledge_base = session.get(KnowledgeBase, knowledge_base_id)
    if knowledge_base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found",
        )

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
    session: Session = Depends(get_session),
) -> None:
    """删除知识库。

    对应接口：DELETE /knowledge-bases/{id}
    """

    knowledge_base = session.get(KnowledgeBase, knowledge_base_id)
    if knowledge_base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found",
        )

    session.delete(knowledge_base)
    session.commit()
