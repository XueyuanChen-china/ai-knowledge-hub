import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session, select

from app.db.database import get_session
from app.db.models import Chunk, KnowledgeBase, KnowledgeItem
from app.schemas.chunk import ChunkRead
from app.schemas.knowledge_item import (
    KnowledgeItemCreate,
    KnowledgeItemChunkResponse,
    KnowledgeItemIndexResponse,
    KnowledgeItemRead,
    KnowledgeItemUpdate,
)
from app.services.text_splitter import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    split_document_text,
)
from app.services.vector_service import add_chunks, delete_vectors

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


def get_knowledge_item_or_404(
    knowledge_item_id: int,
    session: Session,
) -> KnowledgeItem:
    """读取知识条目，不存在就直接抛 404。"""

    knowledge_item = session.get(KnowledgeItem, knowledge_item_id)
    if knowledge_item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge item not found",
        )
    return knowledge_item


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

    return get_knowledge_item_or_404(knowledge_item_id, session)


@router.post(
    "/{knowledge_item_id}/chunks",
    response_model=KnowledgeItemChunkResponse,
    status_code=status.HTTP_201_CREATED,
)
def split_knowledge_item_into_chunks(
    knowledge_item_id: int,
    session: Session = Depends(get_session),
) -> KnowledgeItemChunkResponse:
    """把手动知识条目切成 chunks，并写入 chunks 表。"""

    knowledge_item = get_knowledge_item_or_404(knowledge_item_id, session)

    if not knowledge_item.content.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Knowledge item has no content",
        )

    created_chunks = regenerate_knowledge_item_chunks(knowledge_item, session)
    session.commit()

    return KnowledgeItemChunkResponse(
        knowledge_item_id=knowledge_item.id,
        chunk_count=len(created_chunks),
    )


@router.post(
    "/{knowledge_item_id}/index",
    response_model=KnowledgeItemIndexResponse,
    status_code=status.HTTP_200_OK,
)
def index_knowledge_item(
    knowledge_item_id: int,
    session: Session = Depends(get_session),
) -> KnowledgeItemIndexResponse:
    """切分手动知识条目并写入 PostgreSQL + Elasticsearch。"""

    knowledge_item = get_knowledge_item_or_404(knowledge_item_id, session)

    if not knowledge_item.content.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Knowledge item has no content",
        )

    existing_vector_ids = get_existing_knowledge_item_vector_ids(knowledge_item.id, session)
    if existing_vector_ids:
        delete_vectors(knowledge_item.knowledge_base_id, existing_vector_ids)

    created_chunks = regenerate_knowledge_item_chunks(knowledge_item, session)

    try:
        index_result = add_chunks(created_chunks)
        for chunk, vector_id in zip(created_chunks, index_result.vector_ids):
            chunk.vector_id = vector_id
            session.add(chunk)
        session.commit()
    except Exception:
        session.rollback()
        raise

    return KnowledgeItemIndexResponse(
        knowledge_item_id=knowledge_item.id,
        chunk_count=len(created_chunks),
        vector_count=len(index_result.vector_ids),
        index_name=index_result.index_name,
    )


@router.get("/{knowledge_item_id}/chunks", response_model=list[ChunkRead])
def list_knowledge_item_chunks(
    knowledge_item_id: int,
    session: Session = Depends(get_session),
) -> list[Chunk]:
    """查询某个知识条目下的所有 chunks。

    对应接口：GET /knowledge-items/{knowledge_item_id}/chunks
    """

    knowledge_item = get_knowledge_item_or_404(knowledge_item_id, session)

    statement = (
        select(Chunk)
        .where(Chunk.knowledge_item_id == knowledge_item_id)
        .order_by(Chunk.chunk_index)
    )
    return list(session.exec(statement).all())


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

    knowledge_item = get_knowledge_item_or_404(knowledge_item_id, session)

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

    knowledge_item = get_knowledge_item_or_404(knowledge_item_id, session)

    existing_vector_ids = get_existing_knowledge_item_vector_ids(knowledge_item.id, session)
    if existing_vector_ids:
        delete_vectors(knowledge_item.knowledge_base_id, existing_vector_ids)

    delete_existing_knowledge_item_chunks(knowledge_item.id, session)

    session.delete(knowledge_item)
    session.commit()


def delete_existing_knowledge_item_chunks(knowledge_item_id: int, session: Session) -> None:
    """删除知识条目下已有的 chunk。"""

    statement = select(Chunk).where(Chunk.knowledge_item_id == knowledge_item_id)
    existing_chunks = session.exec(statement).all()
    for chunk in existing_chunks:
        session.delete(chunk)


def get_existing_knowledge_item_vector_ids(
    knowledge_item_id: int,
    session: Session,
) -> list[str]:
    """读取知识条目旧 chunk 的 vector_id。"""

    statement = select(Chunk).where(Chunk.knowledge_item_id == knowledge_item_id)
    existing_chunks = session.exec(statement).all()
    return [chunk.vector_id for chunk in existing_chunks if chunk.vector_id]


def regenerate_knowledge_item_chunks(
    knowledge_item: KnowledgeItem,
    session: Session,
) -> list[Chunk]:
    """重新切分知识条目并创建 chunk 行对象。"""

    chunk_data_list = split_document_text(
        knowledge_item.content,
        "txt",
        chunk_size=DEFAULT_CHUNK_SIZE,
        chunk_overlap=DEFAULT_CHUNK_OVERLAP,
    )

    if not chunk_data_list:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No chunks generated from knowledge item",
        )

    delete_existing_knowledge_item_chunks(knowledge_item.id, session)
    session.flush()

    created_chunks: list[Chunk] = []
    for index, chunk_data in enumerate(chunk_data_list):
        metadata = {
            **chunk_data.metadata,
            "knowledge_item_id": knowledge_item.id,
            "knowledge_item_title": knowledge_item.title,
            "source_type": knowledge_item.source_type,
            "tags": knowledge_item.tags,
            "chunk_index": index,
        }

        chunk_content = chunk_data.content
        if knowledge_item.title.strip() and not chunk_content.lstrip().startswith("#"):
            chunk_content = f"# {knowledge_item.title}\n\n{chunk_content}"
            metadata.setdefault("heading_path", [knowledge_item.title])

        chunk = Chunk(
            knowledge_base_id=knowledge_item.knowledge_base_id,
            document_id=knowledge_item.source_document_id,
            knowledge_item_id=knowledge_item.id,
            chunk_index=index,
            content=chunk_content,
            vector_id=None,
            metadata_json=json.dumps(metadata, ensure_ascii=False),
        )
        session.add(chunk)
        created_chunks.append(chunk)

    session.flush()
    for chunk in created_chunks:
        session.refresh(chunk)

    return created_chunks
