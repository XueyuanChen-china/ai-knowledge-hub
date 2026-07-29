from fastapi import APIRouter, Depends, HTTPException, status
import time
from sqlmodel import Session, select

from app.db.database import get_session
from app.db.models import KnowledgeBase, KnowledgeItem
from app.schemas.search import SemanticSearchRequest, SemanticSearchResult
from app.services.vector_service import search_similar_chunks
from app.observability.metrics import get_metrics
from app.security.dependencies import Principal, require_permission
from app.security.policies import PERMISSION_SEARCH
from app.security.resource_access import get_knowledge_base_or_404

router = APIRouter(prefix="/search", tags=["search"])
search_dependency = require_permission(PERMISSION_SEARCH)


def ensure_knowledge_base_exists(
    knowledge_base_id: int,
    principal: Principal,
    session: Session,
) -> None:
    """确认要检索的知识库存在。"""

    get_knowledge_base_or_404(knowledge_base_id, principal, session)


def build_content_preview(content: str, max_length: int = 200) -> str:
    """生成结果预览文本。"""

    normalized = " ".join(content.split())
    if len(normalized) <= max_length:
        return normalized
    return normalized[: max_length - 3] + "..."


def build_knowledge_item_title_map(
    knowledge_item_ids: set[int],
    organization_id: int,
    session: Session,
) -> dict[int, str]:
    """批量查询知识条目标题，避免逐条 hit 查数据库。"""

    if not knowledge_item_ids:
        return {}

    statement = select(KnowledgeItem).where(
        KnowledgeItem.id.in_(knowledge_item_ids),
        KnowledgeItem.organization_id == organization_id,
    )
    knowledge_items = session.exec(statement).all()
    return {item.id: item.title for item in knowledge_items if item.id is not None}


@router.post("/semantic", response_model=list[SemanticSearchResult])
def semantic_search(
    payload: SemanticSearchRequest,
    principal: Principal = Depends(search_dependency),
    session: Session = Depends(get_session),
) -> list[SemanticSearchResult]:
    """按自然语言问题做语义搜索。"""

    ensure_knowledge_base_exists(payload.knowledge_base_id, principal, session)

    query_text = payload.query.strip()
    if not query_text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Query must not be empty",
        )

    started_at = time.perf_counter()
    try:
        hits = search_similar_chunks(
            principal.organization_id,
            payload.knowledge_base_id,
            query_text,
            top_k=payload.top_k,
        )
    except Exception:
        get_metrics().record_operation(
            "semantic_search",
            time.perf_counter() - started_at,
            outcome="error",
        )
        raise
    get_metrics().record_operation(
        "semantic_search",
        time.perf_counter() - started_at,
        outcome="success",
    )

    knowledge_item_ids = {
        hit.knowledge_item_id for hit in hits if hit.knowledge_item_id is not None
    }
    title_map = build_knowledge_item_title_map(
        knowledge_item_ids,
        principal.organization_id,
        session,
    )

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
