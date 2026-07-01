import re
from typing import Optional

from app.services.document_splitter.models import Block, DocumentElement, PdfPageText, Section
from app.services.document_splitter.parsers.markdown_parser import parse_markdown_elements
from app.services.document_splitter.parsers.plain_text_parser import (
    detect_plain_text_headings,
    detect_plain_text_heading_level,
    is_isolated_plain_text_heading_line,
    is_plain_text_heading_candidate,
    parse_plain_text_elements,
    parse_plain_text_elements_from_pages,
)


def build_sections_from_source(
    text: str,
    file_type: str,
    *,
    pdf_pages: Optional[list[PdfPageText]] = None,
    pdf_path: Optional[str] = None,
    spreadsheet_path: Optional[str] = None,
    word_path: Optional[str] = None,
) -> list[Section]:
    """按输入来源构建 Section 列表。

    Phase 1 先让 Markdown / TXT / PDF 文本 fallback 接到统一入口。
    """

    if file_type == "pdf" and pdf_pages is not None:
        return split_pdf_sections(pdf_pages)
    if file_type == "md":
        return split_markdown_sections(text)
    return split_plain_text_sections(text, file_type)


def build_sections_from_elements(
    elements: list[DocumentElement],
    file_type: str,
) -> list[Section]:
    """从 DocumentElement 列表构建 Section。"""

    if file_type == "md":
        return build_markdown_sections_from_elements(elements)
    return build_plain_text_sections_from_elements(elements, file_type)


def split_markdown_sections(text: str) -> list[Section]:
    elements = parse_markdown_elements(text)
    return build_markdown_sections_from_elements(elements)


def build_markdown_sections_from_elements(
    elements: list[DocumentElement],
) -> list[Section]:
    """从 Markdown DocumentElement 列表构建 Section。"""

    sections: list[Section] = []
    section_boundary_level = detect_markdown_section_boundary_level_from_elements(elements)
    current_elements: list[DocumentElement] = []
    current_heading_path: list[str] = []
    current_level = 0

    for element in elements:
        if element.element_type == "heading":
            heading_level = element.level or 1
            heading_path = list(element.metadata.get("heading_path") or [])

            should_start_new_section = (
                heading_level == section_boundary_level
                and bool(current_elements)
            )
            if should_start_new_section:
                if not is_only_heading_context_elements(
                    current_elements,
                    section_boundary_level,
                ):
                    append_section_from_elements(
                        sections,
                        current_elements,
                        current_heading_path,
                        current_level,
                        file_type="md",
                        splitter="markdown_structure",
                    )
                current_elements = []

            if heading_level == section_boundary_level or not current_heading_path:
                current_heading_path = heading_path.copy()
                current_level = heading_level

        current_elements.append(element)

    append_section_from_elements(
        sections,
        current_elements,
        current_heading_path,
        current_level,
        file_type="md",
        splitter="markdown_structure",
    )

    if not sections:
        return build_plain_text_sections_from_elements(elements, "md")

    return sections


def is_only_heading_context_section(
    lines: list[str],
    section_boundary_level: int,
) -> bool:
    """判断一组行是否只包含主 section 之上的标题上下文。"""

    has_heading = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        heading_match = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if heading_match is None:
            return False

        heading_level = len(heading_match.group(1))
        if heading_level >= section_boundary_level:
            return False

        has_heading = True

    return has_heading


def is_only_heading_context_elements(
    elements: list[DocumentElement],
    section_boundary_level: int,
) -> bool:
    """判断一组 Markdown 元素是否只包含主 section 之上的标题上下文。"""

    has_heading = False

    for element in elements:
        if element.element_type != "heading":
            return False

        heading_level = element.level or 0
        if heading_level >= section_boundary_level:
            return False
        has_heading = True

    return has_heading


def detect_markdown_section_boundary_level(text: str) -> int:
    """判断 Markdown 主 section 默认使用哪一级标题。"""

    in_code_block = False
    has_level_two_heading = False

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue

        if in_code_block:
            continue

        heading_match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading_match and len(heading_match.group(1)) == 2:
            has_level_two_heading = True
            break

    return 2 if has_level_two_heading else 1


def detect_markdown_section_boundary_level_from_elements(
    elements: list[DocumentElement],
) -> int:
    """根据 Markdown heading elements 判断主 section 边界。"""

    for element in elements:
        if element.element_type == "heading" and element.level == 2:
            return 2
    return 1


def append_markdown_section(
    sections: list[Section],
    lines: list[str],
    heading_path: list[str],
    level: int,
    heading_context_path: list[str],
) -> None:
    blocks = build_markdown_blocks(lines, heading_context_path)
    if not blocks and not heading_path:
        return

    sections.append(
        Section(
            heading_path=heading_path.copy(),
            level=level,
            blocks=blocks,
            metadata={
                "file_type": "md",
                "splitter": "markdown_structure",
            },
        )
    )


def append_section_from_elements(
    sections: list[Section],
    elements: list[DocumentElement],
    heading_path: list[str],
    level: int,
    *,
    file_type: str,
    splitter: str,
) -> None:
    """把一组元素真正落成 Section。"""

    blocks = build_blocks_from_elements(elements, file_type=file_type)
    if not blocks and not heading_path:
        return

    sections.append(
        Section(
            heading_path=heading_path.copy(),
            level=level,
            blocks=blocks,
            metadata={
                "file_type": file_type,
                "splitter": splitter,
            },
        )
    )


def split_plain_text_sections(text: str, file_type: str = "txt") -> list[Section]:
    elements = parse_plain_text_elements(text, file_type)
    return build_plain_text_sections_from_elements(elements, file_type)


def build_plain_text_sections_from_elements(
    elements: list[DocumentElement],
    file_type: str,
) -> list[Section]:
    """从纯文本 DocumentElement 列表构建 Section。"""

    heading_indexes = [
        index
        for index, element in enumerate(elements)
        if element.element_type == "heading"
    ]

    if not heading_indexes:
        contextual_sections = build_contextual_sections_without_heading_elements(elements, file_type)
        if contextual_sections:
            return contextual_sections

        return [
            Section(
                heading_path=[],
                level=0,
                blocks=build_blocks_from_elements(elements, file_type=file_type),
                metadata=build_plain_text_section_metadata(
                    elements,
                    file_type=file_type,
                    splitter=build_section_splitter_name(file_type, "plain"),
                ),
            )
        ]

    sections: list[Section] = []
    first_heading_index = heading_indexes[0]
    if any(element.text.strip() for element in elements[:first_heading_index]):
        sections.append(
            Section(
                heading_path=[],
                level=0,
                blocks=build_blocks_from_elements(elements[:first_heading_index], file_type=file_type),
                metadata=build_plain_text_section_metadata(
                    elements[:first_heading_index],
                    file_type=file_type,
                    splitter=build_section_splitter_name(file_type, "plain"),
                ),
            )
        )

    for heading_position_index, start_index in enumerate(heading_indexes):
        end_index = (
            heading_indexes[heading_position_index + 1]
            if heading_position_index + 1 < len(heading_indexes)
            else len(elements)
        )
        section_elements = elements[start_index:end_index]
        heading_element = section_elements[0]
        heading_path = list(heading_element.metadata.get("heading_path") or [heading_element.text])
        level = heading_element.level or 1
        sections.append(
            Section(
                heading_path=heading_path,
                level=level,
                blocks=build_blocks_from_elements(section_elements, file_type=file_type),
                metadata=build_plain_text_section_metadata(
                    section_elements,
                    file_type=file_type,
                    splitter=build_section_splitter_name(file_type, "heading"),
                ),
            )
        )

    return sections


def build_plain_text_section_metadata(
    elements: list[DocumentElement],
    *,
    file_type: str,
    splitter: str,
) -> dict[str, object]:
    """为 plain text / PDF fallback section 生成基础 metadata。"""

    metadata: dict[str, object] = {
        "file_type": file_type,
        "splitter": splitter,
    }

    page_starts = [
        element.page_start
        for element in elements
        if element.page_start is not None
    ]
    page_ends = [
        element.page_end
        for element in elements
        if element.page_end is not None
    ]
    if page_starts:
        metadata["page_start"] = min(page_starts)
    if page_ends:
        metadata["page_end"] = max(page_ends)

    return metadata


def build_contextual_sections_without_heading_elements(
    elements: list[DocumentElement],
    file_type: str,
) -> list[Section]:
    """当没有显式 heading element 时，按已有上下文信息分组 section。"""

    groups: list[tuple[list[str], list[DocumentElement]]] = []
    current_heading_path: Optional[list[str]] = None
    current_elements: list[DocumentElement] = []

    for element in elements:
        heading_path = list(element.metadata.get("heading_path") or [])
        if current_heading_path is None:
            current_heading_path = heading_path
            current_elements = [element]
            continue

        if heading_path != current_heading_path:
            groups.append((current_heading_path, current_elements))
            current_heading_path = heading_path
            current_elements = [element]
            continue

        current_elements.append(element)

    if current_heading_path is not None and current_elements:
        groups.append((current_heading_path, current_elements))

    meaningful_groups = [group for group in groups if group[0]]
    if len(meaningful_groups) <= 1:
        return []

    sections: list[Section] = []
    for heading_path, group_elements in groups:
        sections.append(
            Section(
                heading_path=heading_path,
                level=1 if heading_path else 0,
                blocks=build_blocks_from_elements(group_elements, file_type=file_type),
                metadata=build_plain_text_section_metadata(
                    group_elements,
                    file_type=file_type,
                    splitter=build_section_splitter_name(file_type, "context"),
                ),
            )
        )
    return sections


def build_section_splitter_name(file_type: str, mode: str) -> str:
    """给不同文件类型生成更准确的 section splitter 名称。"""

    if file_type == "md":
        return "markdown_structure"
    if file_type == "pdf":
        return f"pdf_{mode}_structure"
    if file_type == "docx":
        return f"docx_{mode}_structure"
    if file_type == "xlsx":
        return f"xlsx_{mode}_structure"
    if file_type == "csv":
        return f"csv_{mode}_structure"
    return f"{file_type}_{mode}_structure"


def build_plain_text_heading_sections(
    lines: list[str],
    heading_candidates: list[tuple[int, int, str]],
    file_type: str,
) -> list[Section]:
    """按检测到的标题切纯文本 Section。"""

    text = "\n".join(lines)
    elements = parse_plain_text_elements(text, file_type)
    return build_plain_text_sections_from_elements(elements, file_type)


def build_plain_text_heading_blocks(
    lines: list[str],
    *,
    file_type: str,
    heading_text: str,
    heading_level: int,
    heading_path: list[str],
) -> list[Block]:
    """为按标题切出来的纯文本 Section 构造 blocks。"""

    return build_blocks_from_elements(
        parse_plain_text_elements("\n".join(lines), file_type),
        file_type=file_type,
    )


def build_plain_text_paragraph_section(text: str, file_type: str) -> list[Section]:
    """退回原来的纯段落切分模式。"""

    return build_plain_text_sections_from_elements(
        parse_plain_text_elements(text, file_type),
        file_type,
    )


def build_plain_text_paragraph_blocks(
    lines: list[str],
    *,
    file_type: str,
    heading_path: list[str],
    starting_paragraph_index: int = 0,
) -> list[Block]:
    """把纯文本行按空行切成 paragraph blocks。"""

    text = "\n".join(lines)
    elements = parse_plain_text_elements(text, file_type)
    blocks = build_blocks_from_elements(elements, file_type=file_type)
    for index, block in enumerate(blocks):
        block.metadata["heading_path"] = heading_path.copy()
        block.metadata["paragraph_start"] = starting_paragraph_index + index
        block.metadata["paragraph_end"] = starting_paragraph_index + index
    return blocks


def split_pdf_sections(pages: list[PdfPageText]) -> list[Section]:
    """按 PDF 页码和页内段落切 Section / Block。

    Phase 3 先尝试复用 plain text heading detector。
    如果检测到多个可靠标题，就按标题切 section；
    否则继续退回原来的按页 fallback。
    """

    heading_elements = parse_plain_text_elements_from_pages(pages, file_type="pdf")
    if heading_elements is not None:
        return build_plain_text_sections_from_elements(heading_elements, "pdf")

    sections: list[Section] = []
    for page in pages:
        paragraphs = [
            part.strip()
            for part in re.split(r"\n\s*\n+", page.text)
            if part.strip()
        ]
        if not paragraphs and page.text.strip():
            paragraphs = [page.text.strip()]

        blocks: list[Block] = []
        for paragraph_index, paragraph in enumerate(paragraphs):
            blocks.append(
                Block(
                    block_type="paragraph",
                    content=paragraph,
                    metadata={
                        "file_type": "pdf",
                        "block_type": "paragraph",
                        "page_start": page.page_number,
                        "page_end": page.page_number,
                        "paragraph_start": paragraph_index,
                        "paragraph_end": paragraph_index,
                        "splitter": "pdf_page_paragraph_block",
                    },
                )
            )

        if blocks:
            sections.append(
                Section(
                    heading_path=[],
                    level=0,
                    blocks=blocks,
                    metadata={
                        "file_type": "pdf",
                        "page_start": page.page_number,
                        "page_end": page.page_number,
                        "splitter": "pdf_page_structure",
                    },
                )
            )

    return sections


def flatten_sections_to_blocks(sections: list[Section]) -> list[Block]:
    """把 Section 展平成后续 assemble_chunks 可消费的 Block 列表。"""

    blocks: list[Block] = []

    for section_index, section in enumerate(sections):
        for block_index, block in enumerate(section.blocks):
            block_heading_path = block.metadata.get("heading_path") or section.heading_path
            blocks.append(
                Block(
                    block_type=block.block_type,
                    content=block.content,
                    metadata={
                        **section.metadata,
                        **block.metadata,
                        "heading_path": block_heading_path,
                        "section_heading_path": section.heading_path,
                        "section_level": section.level,
                        "section_index": section_index,
                        "block_index": block_index,
                    },
                )
            )

    return blocks


def build_blocks_from_elements(
    elements: list[DocumentElement],
    *,
    file_type: str,
) -> list[Block]:
    """把 DocumentElement 列表转换成 Block 列表。"""

    blocks: list[Block] = []

    for block_index, element in enumerate(elements):
        heading_path = list(element.metadata.get("heading_path") or [])
        splitter = element.metadata.get("splitter", f"{file_type}_element_block")
        blocks.append(
            Block(
                block_type=element.element_type,
                content=element.text.strip(),
                metadata={
                    "file_type": file_type,
                    "block_type": element.element_type,
                    "heading_path": heading_path,
                    "heading_level": element.level,
                    "paragraph_start": block_index,
                    "paragraph_end": block_index,
                    **element.metadata,
                    "splitter": splitter,
                },
            )
        )

    return blocks


def build_markdown_blocks(
    lines: list[str],
    initial_heading_context_path: list[str],
) -> list[Block]:
    """把 Markdown section 解析成 block 列表。"""

    blocks: list[Block] = []
    index = 0
    paragraph_start = 0
    current_heading_stack = initial_heading_context_path.copy()

    while index < len(lines):
        stripped = lines[index].strip()

        if not stripped:
            index += 1
            continue

        heading_match = re.match(r"^(#{1,6})\s+(.+)$", lines[index])
        if heading_match:
            heading_level = len(heading_match.group(1))
            heading = heading_match.group(2).strip()
            current_heading_stack = current_heading_stack[: heading_level - 1]
            current_heading_stack.append(heading)
            blocks.append(
                Block(
                    block_type="heading",
                    content=lines[index].strip(),
                    metadata={
                        "file_type": "md",
                        "block_type": "heading",
                        "heading_path": current_heading_stack.copy(),
                        "heading_level": heading_level,
                        "splitter": "markdown_heading_block",
                        "paragraph_start": paragraph_start,
                        "paragraph_end": paragraph_start,
                    },
                )
            )
            paragraph_start += 1
            index += 1
            continue

        if stripped.startswith("```"):
            code_lines, index = consume_code_block(lines, index)
            blocks.append(
                create_block(
                    "code",
                    code_lines,
                    file_type="md",
                    heading_path=current_heading_stack.copy(),
                    splitter="markdown_code_block",
                    paragraph_start=paragraph_start,
                    paragraph_end=paragraph_start,
                )
            )
            paragraph_start += 1
            continue

        if is_markdown_table_line(stripped):
            table_lines, index = consume_table_block(lines, index)
            blocks.append(
                create_block(
                    "table",
                    table_lines,
                    file_type="md",
                    heading_path=current_heading_stack.copy(),
                    splitter="markdown_table_block",
                    paragraph_start=paragraph_start,
                    paragraph_end=paragraph_start,
                )
            )
            paragraph_start += 1
            continue

        if is_markdown_list_line(stripped):
            list_lines, index = consume_list_block(lines, index)
            blocks.append(
                create_block(
                    "list",
                    list_lines,
                    file_type="md",
                    heading_path=current_heading_stack.copy(),
                    splitter="markdown_list_block",
                    paragraph_start=paragraph_start,
                    paragraph_end=paragraph_start,
                )
            )
            paragraph_start += 1
            continue

        paragraph_lines, index = consume_paragraph_block(lines, index)
        blocks.append(
            create_block(
                "paragraph",
                paragraph_lines,
                file_type="md",
                heading_path=current_heading_stack.copy(),
                splitter="markdown_paragraph_block",
                paragraph_start=paragraph_start,
                paragraph_end=paragraph_start,
            )
        )
        paragraph_start += 1

    return blocks


def create_block(
    block_type: str,
    lines: list[str],
    *,
    file_type: str,
    heading_path: list[str],
    splitter: str,
    paragraph_start: int,
    paragraph_end: int,
) -> Block:
    """创建标准化 Block。"""

    return Block(
        block_type=block_type,
        content="\n".join(lines).strip(),
        metadata={
            "file_type": file_type,
            "block_type": block_type,
            "heading_path": heading_path,
            "paragraph_start": paragraph_start,
            "paragraph_end": paragraph_end,
            "splitter": splitter,
        },
    )


def consume_code_block(lines: list[str], start_index: int) -> tuple[list[str], int]:
    block_lines = [lines[start_index]]
    index = start_index + 1

    while index < len(lines):
        block_lines.append(lines[index])
        if lines[index].strip().startswith("```"):
            index += 1
            break
        index += 1

    return block_lines, index


def consume_table_block(lines: list[str], start_index: int) -> tuple[list[str], int]:
    block_lines = []
    index = start_index

    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped or not is_markdown_table_line(stripped):
            break
        block_lines.append(lines[index])
        index += 1

    return block_lines, index


def consume_list_block(lines: list[str], start_index: int) -> tuple[list[str], int]:
    block_lines = []
    index = start_index

    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped:
            break
        if is_markdown_list_line(stripped) or is_list_continuation_line(lines[index]):
            block_lines.append(lines[index])
            index += 1
            continue
        break

    return block_lines, index


def consume_paragraph_block(lines: list[str], start_index: int) -> tuple[list[str], int]:
    block_lines = []
    index = start_index

    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped:
            break
        if stripped.startswith("```") or is_markdown_table_line(stripped) or is_markdown_list_line(stripped):
            break
        block_lines.append(lines[index])
        index += 1

    return block_lines, index


def is_markdown_table_line(line: str) -> bool:
    return line.startswith("|") and "|" in line[1:]


def is_markdown_list_line(line: str) -> bool:
    return re.match(r"^([-*+]|\d+\.)\s+", line) is not None


def is_list_continuation_line(line: str) -> bool:
    return line.startswith("  ") or line.startswith("\t")
