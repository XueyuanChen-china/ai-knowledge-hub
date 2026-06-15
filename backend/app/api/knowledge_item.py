from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session, select

from app.db.database import get_session
from app.db.models import KnowledgeBase, KnowledgeItem
from app.schemas.knowledge_item import (
    KnowledgeItemCreate,
    KnowledgeItemRead,
    KnowledgeItemUpdate,
)

router = APIRouter(prefix="/knowledge-items", tags=["knowledge-items"])

ALLOWED_KNOWLEDGE_ITEM_STATUSES = {"draft", "active", "disabled"}


def validate_knowledge_item_status(item_status: str) -> None:
    """校验知识条目状态是否合法。"""

    if item_status not in ALLOWED_KNOWLEDGE_ITEM_STATUSES:
        allowed = ", ".join(sorted(ALLOWED_KNOWLEDGE_ITEM_STATUSES))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status. Allowed values: {allowed}",
        )


def ensure_knowledge_base_exists(
    knowledge_base_id: int,
    session: Session,
) -> None:
    """确认知识库存在。

    创建或更新知识条目时，如果 knowledge_base_id 不存在，就不应该写入脏数据。
    """

    knowledge_base = session.get(KnowledgeBase, knowledge_base_id)
    if knowledge_base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found",
        )


@router.post(
    "",
    response_model=KnowledgeItemRead,
    status_code=status.HTTP_201_CREATED,
)
def create_knowledge_item(
    payload: KnowledgeItemCreate,
    session: Session = Depends(get_session),
) -> KnowledgeItem:
    """手动创建知识条目。

    对应接口：POST /knowledge-items
    """

    validate_knowledge_item_status(payload.status)
    ensure_knowledge_base_exists(payload.knowledge_base_id, session)

    knowledge_item = KnowledgeItem(
        knowledge_base_id=payload.knowledge_base_id,
        title=payload.title,
        content=payload.content,
        tags=payload.tags,
        status=payload.status,
        source_type="manual",
        source_document_id=None,
    )

    session.add(knowledge_item)
    session.commit()
    session.refresh(knowledge_item)

    return knowledge_item


@router.get("", response_model=list[KnowledgeItemRead])
def list_knowledge_items(
    knowledge_base_id: Optional[int] = Query(default=None),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    session: Session = Depends(get_session),
) -> list[KnowledgeItem]:
    """查询知识条目列表，支持按知识库和状态过滤。

    对应接口：
    - GET /knowledge-items
    - GET /knowledge-items?knowledge_base_id=1
    - GET /knowledge-items?status=active
    - GET /knowledge-items?knowledge_base_id=1&status=draft
    """

    if status_filter is not None:
        validate_knowledge_item_status(status_filter)

    statement = select(KnowledgeItem)

    if knowledge_base_id is not None:
        statement = statement.where(KnowledgeItem.knowledge_base_id == knowledge_base_id)

    if status_filter is not None:
        statement = statement.where(KnowledgeItem.status == status_filter)

    statement = statement.order_by(KnowledgeItem.created_at.desc())
    return list(session.exec(statement).all())


@router.get("/{knowledge_item_id}", response_model=KnowledgeItemRead)
def get_knowledge_item(
    knowledge_item_id: int,
    session: Session = Depends(get_session),
) -> KnowledgeItem:
    """查询单个知识条目。

    对应接口：GET /knowledge-items/{id}
    """

    knowledge_item = session.get(KnowledgeItem, knowledge_item_id)
    if knowledge_item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge item not found",
        )

    return knowledge_item


@router.put("/{knowledge_item_id}", response_model=KnowledgeItemRead)
def update_knowledge_item(
    knowledge_item_id: int,
    payload: KnowledgeItemUpdate,
    session: Session = Depends(get_session),
) -> KnowledgeItem:
    """编辑知识条目。

    对应接口：PUT /knowledge-items/{id}
    """

    validate_knowledge_item_status(payload.status)
    ensure_knowledge_base_exists(payload.knowledge_base_id, session)

    knowledge_item = session.get(KnowledgeItem, knowledge_item_id)
    if knowledge_item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge item not found",
        )

    knowledge_item.knowledge_base_id = payload.knowledge_base_id
    knowledge_item.title = payload.title
    knowledge_item.content = payload.content
    knowledge_item.tags = payload.tags
    knowledge_item.status = payload.status
    knowledge_item.updated_at = datetime.utcnow()

    session.add(knowledge_item)
    session.commit()
    session.refresh(knowledge_item)

    return knowledge_item


@router.delete("/{knowledge_item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_knowledge_item(
    knowledge_item_id: int,
    session: Session = Depends(get_session),
) -> None:
    """删除知识条目。

    对应接口：DELETE /knowledge-items/{id}
    """

    knowledge_item = session.get(KnowledgeItem, knowledge_item_id)
    if knowledge_item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge item not found",
        )

    session.delete(knowledge_item)
    session.commit()
