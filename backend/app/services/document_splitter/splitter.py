from dataclasses import dataclass
from typing import Optional

from app.services.document_splitter.chunk_assembler import (
    assemble_element_chunks,
    validate_splitter_options,
)
from app.services.document_splitter.models import DocumentElement
from app.services.document_splitter.models import (
    ChunkData,
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_MAX_CHUNK_SIZE,
    DEFAULT_TARGET_CHUNK_SIZE,
    PdfPageText,
)
from app.services.document_splitter.normalizer import normalize_document_text, normalize_file_type
from app.services.document_splitter.normalizer import IdentityDocumentNormalizer
from app.services.document_splitter.parsers import (
    parse_csv_elements,
    parse_docx_elements_from_document,
    parse_excel_elements_from_workbook,
    parse_markdown_elements,
    parse_pdf_layout_elements_from_document,
    parse_plain_text_elements,
    parse_plain_text_elements_from_pages,
)
from app.services.document_splitter.section_builder import (
    assign_section_context,
    build_sections_from_elements,
    build_sections_from_source,
    flatten_sections_to_blocks,
)


@dataclass
class ParsedSplitterSource:
    """统一输入载体。

    当前阶段：
    - Markdown / TXT / CSV / Excel / DOCX 已经会在 parse 阶段生成 DocumentElement
    - PDF 优先走 layout parser，失败再退回 text fallback
    """

    text: str
    file_type: str
    pdf_pages: Optional[list[PdfPageText]] = None
    pdf_path: Optional[str] = None
    spreadsheet_path: Optional[str] = None
    word_path: Optional[str] = None
    elements: Optional[list[DocumentElement]] = None


def parse_splitter_source(
    text: str,
    file_type: str,
    *,
    pdf_pages: Optional[list[PdfPageText]] = None,
    pdf_path: Optional[str] = None,
    spreadsheet_path: Optional[str] = None,
    word_path: Optional[str] = None,
) -> ParsedSplitterSource:
    """parse 阶段。

    这一层负责把原始文本先标准化，再按文件类型转成统一 source。
    其中 Markdown / TXT / CSV / Excel / DOCX 会直接进入 DocumentElement 体系；
    PDF 优先走 layout parser，失败时再退回已有 fallback。
    """

    normalized_file_type = normalize_file_type(file_type)
    normalized_text = normalize_document_text(text)

    elements: Optional[list[DocumentElement]] = None
    if normalized_file_type == "md":
        elements = parse_markdown_elements(normalized_text)
    elif normalized_file_type == "csv":
        elements = parse_csv_elements(normalized_text, normalized_file_type)
    elif normalized_file_type == "xlsx" and spreadsheet_path is not None:
        elements = parse_excel_elements_from_workbook(spreadsheet_path, normalized_file_type)
    elif normalized_file_type == "xlsx":
        elements = parse_plain_text_elements(normalized_text, normalized_file_type)
    elif normalized_file_type == "docx" and word_path is not None:
        elements = parse_docx_elements_from_document(word_path, normalized_file_type)
    elif normalized_file_type == "docx":
        elements = parse_plain_text_elements(normalized_text, normalized_file_type)
    elif normalized_file_type == "pdf" and pdf_path is not None:
        elements = parse_pdf_layout_elements_from_document(pdf_path, normalized_file_type)
        if elements is None and pdf_pages is not None:
            elements = parse_plain_text_elements_from_pages(pdf_pages, normalized_file_type)
    elif normalized_file_type == "pdf" and pdf_pages is not None:
        elements = parse_plain_text_elements_from_pages(pdf_pages, normalized_file_type)
    elif normalized_file_type != "pdf":
        elements = parse_plain_text_elements(normalized_text, normalized_file_type)

    return ParsedSplitterSource(
        text=normalized_text,
        file_type=normalized_file_type,
        pdf_pages=pdf_pages,
        pdf_path=pdf_path,
        spreadsheet_path=spreadsheet_path,
        word_path=word_path,
        elements=elements,
    )


def normalize_splitter_source(source: ParsedSplitterSource) -> ParsedSplitterSource:
    """normalize 阶段。"""

    normalizer = IdentityDocumentNormalizer()

    return ParsedSplitterSource(
        text=normalize_document_text(source.text),
        file_type=source.file_type,
        pdf_pages=source.pdf_pages,
        pdf_path=source.pdf_path,
        spreadsheet_path=source.spreadsheet_path,
        word_path=source.word_path,
        elements=normalizer.normalize(source.elements or []) if source.elements is not None else None,
    )


def build_document_sections(source: ParsedSplitterSource):
    """build_sections 阶段。"""

    if source.elements is not None:
        return build_sections_from_elements(source.elements, source.file_type)

    return build_sections_from_source(
        source.text,
        source.file_type,
        pdf_pages=source.pdf_pages,
        pdf_path=source.pdf_path,
        spreadsheet_path=source.spreadsheet_path,
        word_path=source.word_path,
    )


def build_document_elements(source: ParsedSplitterSource) -> list[DocumentElement]:
    """返回带 section context 的主链路 Element 列表。

    这是 Parser 与 ChunkAssembler 之间的新主接口。Section/Block 仍可通过
    build_document_sections/build_document_blocks 获取，用于回归快照和兼容调用。
    """

    if source.elements is None:
        return []
    return assign_section_context(source.elements, source.file_type)


def build_document_blocks(source: ParsedSplitterSource):
    """build_blocks 阶段。"""

    sections = build_document_sections(source)
    return flatten_sections_to_blocks(sections)


def split_document_text(
    text: str,
    file_type: str,
    *,
    chunk_size: Optional[int] = None,
    target_chunk_size: int = DEFAULT_TARGET_CHUNK_SIZE,
    max_chunk_size: int = DEFAULT_MAX_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    pdf_pages: Optional[list[PdfPageText]] = None,
    pdf_path: Optional[str] = None,
    spreadsheet_path: Optional[str] = None,
    word_path: Optional[str] = None,
) -> list[ChunkData]:
    """统一 splitter pipeline。"""

    resolved_max_chunk_size = chunk_size or max_chunk_size
    resolved_target_chunk_size = min(target_chunk_size, resolved_max_chunk_size)

    validate_splitter_options(
        resolved_target_chunk_size,
        resolved_max_chunk_size,
        chunk_overlap,
    )

    parsed_source = parse_splitter_source(
        text,
        file_type,
        pdf_pages=pdf_pages,
        pdf_path=pdf_path,
        spreadsheet_path=spreadsheet_path,
        word_path=word_path,
    )
    normalized_source = normalize_splitter_source(parsed_source)
    elements = build_document_elements(normalized_source)

    return assemble_element_chunks(
        elements,
        target_chunk_size=resolved_target_chunk_size,
        max_chunk_size=resolved_max_chunk_size,
        chunk_overlap=chunk_overlap,
    )
