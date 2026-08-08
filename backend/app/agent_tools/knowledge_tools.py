"""知识库只读工具实现。

每个 handler 都显式接收 organization_id 和 knowledge_base_id，并在数据库查询中
使用这两个条件。工具不能依赖模型自己传入组织 ID，也不会返回文件存储路径或密钥。
"""

import json
from datetime import datetime
from typing import Any, Dict, List, Tuple

from sqlmodel import Session, select

from app.agent_tools.schemas import (
    GetChunkNeighborsArgs,
    GetDocumentArgs,
    GetKnowledgeItemArgs,
    ListKnowledgeBaseDocumentsArgs,
    SearchKnowledgeBaseArgs,
    ToolExecutionContext,
)
from app.config import get_settings
from app.db.models import Chunk, Document, KnowledgeItem
from app.services import rag_service


class KnowledgeToolError(Exception):
    """可安全返回给 Agent 的工具业务错误。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def search_knowledge_base(
    session: Session,
    context: ToolExecutionContext,
    arguments: SearchKnowledgeBaseArgs,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """在当前组织和知识库内执行一次受控混合检索。"""

    documents = rag_service.retrieve(
        arguments.query,
        context.knowledge_base_id,
        session,
        organization_id=context.organization_id,
        top_k=arguments.top_k,
    )
    return _retrieved_documents_payload(documents)


def get_document(
    session: Session,
    context: ToolExecutionContext,
    arguments: GetDocumentArgs,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    statement = select(Document).where(
        Document.id == arguments.document_id,
        Document.organization_id == context.organization_id,
        Document.knowledge_base_id == context.knowledge_base_id,
    )
    document = session.exec(statement).first()
    if document is None:
        raise KnowledgeToolError("not_found", "document not found in current scope")

    max_chars = max(1000, get_settings().agent_tool_max_document_chars)
    content = str(document.extracted_text or "")
    clipped = len(content) > max_chars
    return (
        {
            "document_id": document.id,
            "filename": document.filename,
            "file_type": document.file_type,
            "status": document.status,
            "content": content[:max_chars],
            "content_truncated": clipped,
            "created_at": _isoformat(document.created_at),
        },
        [
            {
                "doc_id": document.id,
                "chunk_id": None,
                "knowledge_item_id": None,
                "title": document.filename,
                "score": 1.0,
                "source": "tool:get_document",
            }
        ],
    )


def get_knowledge_item(
    session: Session,
    context: ToolExecutionContext,
    arguments: GetKnowledgeItemArgs,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    statement = select(KnowledgeItem).where(
        KnowledgeItem.id == arguments.knowledge_item_id,
        KnowledgeItem.organization_id == context.organization_id,
        KnowledgeItem.knowledge_base_id == context.knowledge_base_id,
    )
    item = session.exec(statement).first()
    if item is None:
        raise KnowledgeToolError("not_found", "knowledge item not found in current scope")

    max_chars = max(1000, get_settings().agent_tool_max_document_chars)
    content = str(item.content or "")
    return (
        {
            "knowledge_item_id": item.id,
            "title": item.title,
            "status": item.status,
            "source_type": item.source_type,
            "tags": item.tags,
            "content": content[:max_chars],
            "content_truncated": len(content) > max_chars,
            "created_at": _isoformat(item.created_at),
        },
        [
            {
                "doc_id": item.source_document_id,
                "chunk_id": None,
                "knowledge_item_id": item.id,
                "title": item.title,
                "score": 1.0,
                "source": "tool:get_knowledge_item",
            }
        ],
    )


def get_chunk_neighbors(
    session: Session,
    context: ToolExecutionContext,
    arguments: GetChunkNeighborsArgs,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    center_statement = select(Chunk).where(
        Chunk.id == arguments.chunk_id,
        Chunk.organization_id == context.organization_id,
        Chunk.knowledge_base_id == context.knowledge_base_id,
    )
    center = session.exec(center_statement).first()
    if center is None:
        raise KnowledgeToolError("not_found", "chunk not found in current scope")

    statement = select(Chunk).where(
        Chunk.organization_id == context.organization_id,
        Chunk.knowledge_base_id == context.knowledge_base_id,
        Chunk.knowledge_item_id == center.knowledge_item_id,
        Chunk.chunk_index >= max(0, center.chunk_index - arguments.radius),
        Chunk.chunk_index <= center.chunk_index + arguments.radius,
    ).order_by(Chunk.chunk_index)
    neighbors = session.exec(statement).all()
    payload_items: List[Dict[str, Any]] = []
    citations: List[Dict[str, Any]] = []
    for chunk in neighbors:
        metadata = _parse_metadata(chunk.metadata_json)
        payload_items.append(
            {
                "chunk_id": chunk.id,
                "chunk_index": chunk.chunk_index,
                "content": chunk.content,
                "metadata": metadata,
            }
        )
        citations.append(
            {
                "doc_id": chunk.document_id,
                "chunk_id": chunk.id,
                "knowledge_item_id": chunk.knowledge_item_id,
                "title": str(metadata.get("filename") or "Knowledge Chunk"),
                "score": 1.0,
                "source": "tool:get_chunk_neighbors",
            }
        )
    return (
        {
            "center_chunk_id": center.id,
            "radius": arguments.radius,
            "chunks": payload_items,
        },
        citations,
    )


def list_knowledge_base_documents(
    session: Session,
    context: ToolExecutionContext,
    arguments: ListKnowledgeBaseDocumentsArgs,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    statement = (
        select(Document)
        .where(
            Document.organization_id == context.organization_id,
            Document.knowledge_base_id == context.knowledge_base_id,
        )
        .order_by(Document.created_at.desc())
        .limit(arguments.limit)
    )
    documents = session.exec(statement).all()
    return (
        {
            "knowledge_base_id": context.knowledge_base_id,
            "documents": [
                {
                    "document_id": document.id,
                    "filename": document.filename,
                    "file_type": document.file_type,
                    "status": document.status,
                    "created_at": _isoformat(document.created_at),
                }
                for document in documents
            ],
        },
        [],
    )


def _retrieved_documents_payload(
    documents: List[rag_service.RetrievedDocument],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    return (
        {
            "results": [
                {
                    "doc_id": document.doc_id,
                    "chunk_id": document.chunk_id,
                    "knowledge_item_id": document.knowledge_item_id,
                    "title": document.title,
                    "content": document.content,
                    "score": document.score,
                    "metadata": document.metadata,
                }
                for document in documents
            ]
        },
        rag_service.build_citations(documents),
    )


def _parse_metadata(value: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _isoformat(value: datetime) -> str:
    return value.isoformat() if value else ""
