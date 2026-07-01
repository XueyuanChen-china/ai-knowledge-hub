import re

from app.services.document_splitter.models import DocumentElement
from app.services.document_splitter.normalizer import normalize_document_text


def parse_markdown_elements(text: str) -> list[DocumentElement]:
    """把 Markdown 文本解析成 DocumentElement 列表。"""

    normalized_text = normalize_document_text(text)
    lines = normalized_text.splitlines()

    elements: list[DocumentElement] = []
    current_heading_stack: list[str] = []
    index = 0
    source_index = 0

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
            elements.append(
                DocumentElement(
                    source_index=source_index,
                    element_type="heading",
                    text=lines[index].strip(),
                    level=heading_level,
                    metadata={
                        "file_type": "md",
                        "heading_path": current_heading_stack.copy(),
                        "heading_level": heading_level,
                        "block_type": "heading",
                        "splitter": "markdown_heading_block",
                        "source_parser": "markdown_parser",
                    },
                )
            )
            source_index += 1
            index += 1
            continue

        if stripped.startswith("```"):
            code_lines, index = consume_code_block(lines, index)
            elements.append(
                create_markdown_element(
                    "code",
                    code_lines,
                    source_index=source_index,
                    heading_path=current_heading_stack,
                    splitter="markdown_code_block",
                )
            )
            source_index += 1
            continue

        if is_markdown_table_line(stripped):
            table_lines, index = consume_table_block(lines, index)
            elements.append(
                create_markdown_element(
                    "table",
                    table_lines,
                    source_index=source_index,
                    heading_path=current_heading_stack,
                    splitter="markdown_table_block",
                )
            )
            source_index += 1
            continue

        if is_markdown_list_line(stripped):
            list_lines, index = consume_list_block(lines, index)
            elements.append(
                create_markdown_element(
                    "list",
                    list_lines,
                    source_index=source_index,
                    heading_path=current_heading_stack,
                    splitter="markdown_list_block",
                )
            )
            source_index += 1
            continue

        paragraph_lines, index = consume_paragraph_block(lines, index)
        elements.append(
            create_markdown_element(
                "paragraph",
                paragraph_lines,
                source_index=source_index,
                heading_path=current_heading_stack,
                splitter="markdown_paragraph_block",
            )
        )
        source_index += 1

    return elements


def create_markdown_element(
    element_type: str,
    lines: list[str],
    *,
    source_index: int,
    heading_path: list[str],
    splitter: str,
) -> DocumentElement:
    return DocumentElement(
        source_index=source_index,
        element_type=element_type,
        text="\n".join(lines).strip(),
        metadata={
            "file_type": "md",
            "heading_path": heading_path.copy(),
            "block_type": element_type,
            "splitter": splitter,
            "source_parser": "markdown_parser",
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
