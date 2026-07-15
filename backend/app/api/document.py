import json
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from pypdf import PdfReader
from sqlmodel import Session, select

from app.db.database import get_session
from app.db.models import Chunk, Document, KnowledgeBase, KnowledgeItem
from app.schemas.chunk import ChunkRead
from app.schemas.document import DocumentChunkResponse, DocumentIndexResponse, DocumentRead
from app.services.text_splitter import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    PdfPageText,
    split_document_text,
)
from app.services.document_splitter.parsers.docx_parser import document_to_text
from app.services.document_splitter.parsers.excel_parser import workbook_to_text
from app.services.document_splitter.parsers.pdf_layout_parser import pdf_layout_document_to_text
from app.services.vector_service import add_chunks, delete_vectors

router = APIRouter(prefix="/documents", tags=["documents"])

UPLOAD_DIR = Path("data/uploads")
ALLOWED_FILE_EXTENSIONS = {".txt", ".md", ".pdf", ".xlsx", ".docx"}


def ensure_knowledge_base_exists(
    knowledge_base_id: int,
    session: Session,
) -> None:
    """确认上传文件要归属的知识库存在。"""

    knowledge_base = session.get(KnowledgeBase, knowledge_base_id)
    if knowledge_base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found",
        )


def validate_upload_file(file: UploadFile) -> str:
    """校验上传文件类型，并返回文件后缀。"""

    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()

    if suffix not in ALLOWED_FILE_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_FILE_EXTENSIONS))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only {allowed} files are supported",
        )

    return suffix


def extract_text_from_file(file_path: Path, suffix: str) -> str:
    """从 txt / md / pdf / xlsx / docx 文件中提取纯文本。"""

    if suffix in {".txt", ".md"}:
        return file_path.read_text(encoding="utf-8")

    if suffix == ".pdf":
        layout_text = pdf_layout_document_to_text(str(file_path))
        if layout_text:
            return layout_text

        reader = PdfReader(str(file_path))
        page_texts = []

        for page in reader.pages:
            page_text = page.extract_text() or ""
            if page_text.strip():
                page_texts.append(page_text.strip())

        return "\n\n".join(page_texts)

    if suffix == ".xlsx":
        return workbook_to_text(str(file_path))

    if suffix == ".docx":
        return document_to_text(str(file_path))

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Unsupported file type",
    )


def extract_pdf_pages(file_path: Path) -> list[PdfPageText]:
    """按页提取 PDF 文本，并保留页码。"""

    reader = PdfReader(str(file_path))
    pages = []

    for index, page in enumerate(reader.pages, start=1):
        page_text = page.extract_text() or ""
        pages.append(PdfPageText(page_number=index, text=page_text))

    return pages


@router.post(
    "",
    response_model=DocumentRead,
    status_code=status.HTTP_201_CREATED,
)
def upload_document(
    knowledge_base_id: int = Form(...),
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
) -> Document:
    """上传 txt / md / pdf / xlsx / docx 文件。

    对应接口：POST /documents
    上传成功后：
    - 文件保存到 backend/data/uploads
    - 提取文本保存到 documents.extracted_text
    - documents 表写入一条记录
    - 返回 document_id，也就是响应里的 id
    """

    ensure_knowledge_base_exists(knowledge_base_id, session)
    suffix = validate_upload_file(file)

    original_filename = Path(file.filename or "upload").name
    saved_filename = f"{uuid4().hex}_{original_filename}"
    upload_path = UPLOAD_DIR / saved_filename
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    try:
        with upload_path.open("wb") as output_file:
            while chunk := file.file.read(1024 * 1024):
                output_file.write(chunk)
    finally:
        file.file.close()

    try:
        extracted_text = extract_text_from_file(upload_path, suffix)
    # 如果发生了 UnicodeDecodeError，就把这个错误对象保存到变量 exc 里。
    except UnicodeDecodeError as exc:
        # 作用是：删除刚刚上传到本地的文件。missing_ok=True 的意思是：如果文件已经不存在，也不要报错
        upload_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File text encoding must be UTF-8",
        ) from exc

    document = Document(
        knowledge_base_id=knowledge_base_id,
        filename=original_filename,
        file_path=str(upload_path),
        file_type=suffix.removeprefix("."),
        status="uploaded",
        extracted_text=extracted_text,
    )

    session.add(document)
    session.commit()
    session.refresh(document)

    return document


@router.get("", response_model=list[DocumentRead])
def list_documents(
    knowledge_base_id: Optional[int] = Query(default=None),
    session: Session = Depends(get_session),
) -> list[Document]:
    """查询文档列表。

    对应接口：GET /documents
    当前前端文档页会用这个接口展示上传结果，并支持按 knowledge_base_id 过滤。
    """

    statement = select(Document)
    if knowledge_base_id is not None:
        statement = statement.where(Document.knowledge_base_id == knowledge_base_id)

    statement = statement.order_by(Document.created_at.desc(), Document.id.desc())
    return list(session.exec(statement).all())


@router.post(
    "/{document_id}/chunks",
    response_model=DocumentChunkResponse,
    status_code=status.HTTP_201_CREATED,
)
def split_document_into_chunks(
    document_id: int,
    session: Session = Depends(get_session),
) -> DocumentChunkResponse:
    """把文档提取文本切成 chunks，并写入 chunks 表。

    对应接口：POST /documents/{document_id}/chunks
    Day 8 先采用手动触发，方便先确认 extracted_text 是否正确。
    """

    document = session.get(Document, document_id)
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    if not document.extracted_text.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Document has no extracted text",
        )

    pdf_pages = None
    if document.file_type == "pdf":
        pdf_pages = extract_pdf_pages(Path(document.file_path))

    knowledge_item, created_chunks = regenerate_document_chunks(
        document,
        session,
        pdf_pages=pdf_pages,
    )
    session.commit()

    return DocumentChunkResponse(
        document_id=document.id,
        knowledge_item_id=knowledge_item.id,
        chunk_count=len(created_chunks),
    )


@router.post(
    "/{document_id}/index",
    response_model=DocumentIndexResponse,
    status_code=status.HTTP_200_OK,
)
def index_document(
    document_id: int,
    session: Session = Depends(get_session),
) -> DocumentIndexResponse:
    """切 chunk 并写入 PostgreSQL + Elasticsearch。

    对应接口：POST /documents/{document_id}/index
    Day 9 先按文档维度做手动触发，方便在 Swagger 里验收。
    """

    document = session.get(Document, document_id)
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    if not document.extracted_text.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Document has no extracted text",
        )

    pdf_pages = None
    if document.file_type == "pdf":
        pdf_pages = extract_pdf_pages(Path(document.file_path))

    existing_vector_ids = get_existing_document_vector_ids(document.id, session)
    if existing_vector_ids:
        delete_vectors(document.knowledge_base_id, existing_vector_ids)

    knowledge_item, created_chunks = regenerate_document_chunks(
        document,
        session,
        pdf_pages=pdf_pages,
    )

    try:
        index_result = add_chunks(created_chunks)
        for chunk, vector_id in zip(created_chunks, index_result.vector_ids):
            chunk.vector_id = vector_id
            session.add(chunk)

        document.status = "indexed"
        session.add(document)
        session.commit()
    except Exception:
        session.rollback()
        refreshed_document = session.get(Document, document.id)
        if refreshed_document is not None:
            # 索引失败时显式标成 failed，前端才能直接看出这次构建没有成功。
            refreshed_document.status = "failed"
            session.add(refreshed_document)
            session.commit()
        raise

    return DocumentIndexResponse(
        document_id=document.id,
        knowledge_item_id=knowledge_item.id,
        chunk_count=len(created_chunks),
        vector_count=len(index_result.vector_ids),
        index_name=index_result.index_name,
    )


@router.get("/{document_id}/chunks", response_model=list[ChunkRead])
def list_document_chunks(
    document_id: int,
    session: Session = Depends(get_session),
) -> list[Chunk]:
    """查询某个文档生成的所有 chunks。

    对应接口：GET /documents/{document_id}/chunks
    这个接口用于在 Swagger 里直接验收切分结果，不必打开 DB Browser。
    """

    document = session.get(Document, document_id)
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    statement = (
        select(Chunk)
        .where(Chunk.document_id == document_id)
        .order_by(Chunk.chunk_index)
    )
    return list(session.exec(statement).all())


def get_or_create_document_knowledge_item(
    document: Document,
    session: Session,
) -> KnowledgeItem:
    """为文档创建或复用一个 KnowledgeItem。"""

    statement = select(KnowledgeItem).where(
        KnowledgeItem.source_type == "document",
        KnowledgeItem.source_document_id == document.id,
    )
    knowledge_item = session.exec(statement).first()

    if knowledge_item is not None:
        knowledge_item.title = document.filename
        knowledge_item.content = document.extracted_text
        knowledge_item.updated_at = datetime.utcnow()
        session.add(knowledge_item)
        session.flush()
        return knowledge_item

    knowledge_item = KnowledgeItem(
        knowledge_base_id=document.knowledge_base_id,
        title=document.filename,
        content=document.extracted_text,
        tags="[]",
        status="draft",
        source_type="document",
        source_document_id=document.id,
    )
    session.add(knowledge_item)
    session.flush()
    session.refresh(knowledge_item)

    return knowledge_item


def delete_existing_document_chunks(document_id: int, session: Session) -> None:
    """重复触发切分时，先清理旧 chunk，避免重复写入。"""

    statement = select(Chunk).where(Chunk.document_id == document_id)
    existing_chunks = session.exec(statement).all()
    for chunk in existing_chunks:
        session.delete(chunk)


def get_existing_document_vector_ids(
    document_id: int,
    session: Session,
) -> list[str]:
    """读取文档旧 chunk 的 vector_id，供重建索引时先删 Elasticsearch 中的旧向量。"""

    statement = select(Chunk).where(Chunk.document_id == document_id)
    existing_chunks = session.exec(statement).all()
    return [chunk.vector_id for chunk in existing_chunks if chunk.vector_id]


def regenerate_document_chunks(
    document: Document,
    session: Session,
    *,
    pdf_pages: Optional[list[PdfPageText]] = None,
) -> tuple[KnowledgeItem, list[Chunk]]:
    """重新切分文档并创建 chunk 行对象。

    这个函数只负责把 chunk 写到当前 Session，不负责 commit。
    这样 /chunks 和 /index 都能复用同一套生成逻辑。
    """

    chunk_data_list = split_document_text(
        document.extracted_text,
        document.file_type,
        chunk_size=DEFAULT_CHUNK_SIZE,
        chunk_overlap=DEFAULT_CHUNK_OVERLAP,
        pdf_pages=pdf_pages,
        pdf_path=document.file_path if document.file_type == "pdf" else None,
        spreadsheet_path=document.file_path if document.file_type == "xlsx" else None,
        word_path=document.file_path if document.file_type == "docx" else None,
    )

    if not chunk_data_list:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No chunks generated from document",
        )

    knowledge_item = get_or_create_document_knowledge_item(document, session)
    delete_existing_document_chunks(document.id, session)
    session.flush()

    created_chunks: list[Chunk] = []
    for index, chunk_data in enumerate(chunk_data_list):
        metadata = {
            **chunk_data.metadata,
            "document_id": document.id,
            "filename": document.filename,
            "file_type": document.file_type,
            "knowledge_item_id": knowledge_item.id,
            "chunk_index": index,
        }

        chunk = Chunk(
            knowledge_base_id=document.knowledge_base_id,
            document_id=document.id,
            knowledge_item_id=knowledge_item.id,
            chunk_index=index,
            content=chunk_data.content,
            vector_id=None,
            metadata_json=json.dumps(metadata, ensure_ascii=False),
        )
        session.add(chunk)
        created_chunks.append(chunk)

    session.flush()
    for chunk in created_chunks:
        session.refresh(chunk)

    return knowledge_item, created_chunks
