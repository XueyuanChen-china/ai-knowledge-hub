from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from app.db.database import get_session
from app.db.models import KnowledgeBase, KnowledgeItem
from app.schemas.search import SemanticSearchRequest, SemanticSearchResult
from app.services.vector_service import search_similar_chunks

router = APIRouter(prefix="/search", tags=["search"])


def ensure_knowledge_base_exists(
    knowledge_base_id: int,
    session: Session,
) -> None:
    """确认要检索的知识库存在。"""

    knowledge_base = session.get(KnowledgeBase, knowledge_base_id)
    if knowledge_base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found",
        )


def build_content_preview(content: str, max_length: int = 200) -> str:
    """生成结果预览文本。"""

    normalized = " ".join(content.split())
    if len(normalized) <= max_length:
        return normalized
    return normalized[: max_length - 3] + "..."


def build_knowledge_item_title_map(
    knowledge_item_ids: set[int],
    session: Session,
) -> dict[int, str]:
    """批量查询知识条目标题，避免逐条 hit 查数据库。"""

    if not knowledge_item_ids:
        return {}

    statement = select(KnowledgeItem).where(KnowledgeItem.id.in_(knowledge_item_ids))
    knowledge_items = session.exec(statement).all()
    return {item.id: item.title for item in knowledge_items if item.id is not None}


@router.post("/semantic", response_model=list[SemanticSearchResult])
def semantic_search(
    payload: SemanticSearchRequest,
    session: Session = Depends(get_session),
) -> list[SemanticSearchResult]:
    """按自然语言问题做语义搜索。"""

    ensure_knowledge_base_exists(payload.knowledge_base_id, session)

    query_text = payload.query.strip()
    if not query_text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Query must not be empty",
        )

    hits = search_similar_chunks(
        payload.knowledge_base_id,
        query_text,
        top_k=payload.top_k,
    )

    knowledge_item_ids = {
        hit.knowledge_item_id for hit in hits if hit.knowledge_item_id is not None
    }
    title_map = build_knowledge_item_title_map(knowledge_item_ids, session)

    results: list[SemanticSearchResult] = []
    for hit in hits:
        title = title_map.get(hit.knowledge_item_id or 0)
        if not title:
            title = str(hit.metadata.get("filename") or f"Knowledge Item {hit.knowledge_item_id}")

        results.append(
            SemanticSearchResult(
                doc_id=hit.document_id,
                chunk_id=hit.chunk_id,
                title=title,
                content_preview=build_content_preview(hit.content),
                score=hit.score,
                metadata=hit.metadata,
            )
        )

    return results
