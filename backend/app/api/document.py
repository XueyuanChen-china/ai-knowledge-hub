from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pypdf import PdfReader
from sqlmodel import Session

from app.db.database import get_session
from app.db.models import Document, KnowledgeBase
from app.schemas.document import DocumentRead

router = APIRouter(prefix="/documents", tags=["documents"])

UPLOAD_DIR = Path("data/uploads")
ALLOWED_FILE_EXTENSIONS = {".txt", ".md", ".pdf"}


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
    """从 txt / md / pdf 文件中提取纯文本。"""

    if suffix in {".txt", ".md"}:
        return file_path.read_text(encoding="utf-8")

    if suffix == ".pdf":
        reader = PdfReader(str(file_path))
        page_texts = []

        for page in reader.pages:
            page_text = page.extract_text() or ""
            if page_text.strip():
                page_texts.append(page_text.strip())

        return "\n\n".join(page_texts)

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Unsupported file type",
    )


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
    """上传 txt / md / pdf 文件。

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
