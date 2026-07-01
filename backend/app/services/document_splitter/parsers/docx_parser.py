import re
from typing import Optional

from app.services.document_splitter.models import DocumentElement


def parse_docx_elements_from_document(
    document_path: str,
    file_type: str = "docx",
) -> list[DocumentElement]:
    """把 DOCX 文档解析成 DocumentElement 列表。"""

    document_module = load_python_docx()
    document = document_module.Document(document_path)

    current_heading_stack: list[str] = []
    elements: list[DocumentElement] = []
    source_index = 0
    block_items = list(iter_block_items(document))
    index = 0

    while index < len(block_items):
        block_item = block_items[index]

        if is_docx_paragraph(block_item):
            paragraph = block_item
            paragraph_text = normalize_docx_paragraph_text(paragraph.text)
            if not paragraph_text:
                index += 1
                continue

            heading_level = detect_docx_heading_level(paragraph)
            if heading_level is not None:
                current_heading_stack = current_heading_stack[: max(heading_level - 1, 0)]
                current_heading_stack.append(paragraph_text)
                elements.append(
                    DocumentElement(
                        source_index=source_index,
                        element_type="heading",
                        text=paragraph_text,
                        level=heading_level,
                        metadata={
                            "file_type": file_type,
                            "heading_path": current_heading_stack.copy(),
                            "heading_level": heading_level,
                            "block_type": "heading",
                            "style_name": get_docx_style_name(paragraph),
                            "splitter": "docx_heading_block",
                            "source_parser": "docx_parser",
                        },
                    )
                )
                source_index += 1
                index += 1
                continue

            if is_docx_list_paragraph(paragraph):
                list_lines: list[str] = []
                list_levels: list[int] = []
                list_style_name = get_docx_style_name(paragraph)

                while index < len(block_items):
                    candidate_item = block_items[index]
                    if not is_docx_paragraph(candidate_item):
                        break
                    if detect_docx_heading_level(candidate_item) is not None:
                        break
                    if not is_docx_list_paragraph(candidate_item):
                        break

                    candidate_text = normalize_docx_paragraph_text(candidate_item.text)
                    if candidate_text:
                        list_level = detect_docx_list_level(candidate_item)
                        list_levels.append(list_level)
                        list_lines.append(format_docx_list_line(candidate_text, list_level))
                    index += 1

                if list_lines:
                    elements.append(
                        DocumentElement(
                            source_index=source_index,
                            element_type="list",
                            text="\n".join(list_lines).strip(),
                            level=min(list_levels) if list_levels else None,
                            metadata={
                                "file_type": file_type,
                                "heading_path": current_heading_stack.copy(),
                                "block_type": "list",
                                "style_name": list_style_name,
                                "list_level": min(list_levels) if list_levels else 1,
                                "splitter": "docx_list_block",
                                "source_parser": "docx_parser",
                            },
                        )
                    )
                    source_index += 1
                continue

            elements.append(
                DocumentElement(
                    source_index=source_index,
                    element_type="paragraph",
                    text=paragraph_text,
                    metadata={
                        "file_type": file_type,
                        "heading_path": current_heading_stack.copy(),
                        "block_type": "paragraph",
                        "style_name": get_docx_style_name(paragraph),
                        "splitter": "docx_paragraph_block",
                        "source_parser": "docx_parser",
                    },
                )
            )
            source_index += 1
            index += 1
            continue

        table = block_item
        rows = extract_docx_table_rows(table)
        if not rows:
            index += 1
            continue

        has_header = detect_docx_table_header(rows)
        content = format_docx_table_as_markdown(rows, has_header)
        row_start = 2 if has_header else 1
        row_end = len(rows)

        elements.append(
            DocumentElement(
                source_index=source_index,
                element_type="table",
                text=content,
                row_start=row_start,
                row_end=row_end,
                col_start="A",
                col_end=column_index_to_name(len(rows[0]) - 1),
                metadata={
                    "file_type": file_type,
                    "heading_path": current_heading_stack.copy(),
                    "block_type": "table",
                    "has_header": has_header,
                    "header_row_count": 1 if has_header else 0,
                    "header_values": rows[0] if has_header else [],
                    "row_start": row_start,
                    "row_end": row_end,
                    "row_count": row_end - row_start + 1 if row_end >= row_start else 0,
                    "column_count": len(rows[0]),
                    "col_start": "A",
                    "col_end": column_index_to_name(len(rows[0]) - 1),
                    "table_format": "docx_markdown",
                    "splitter": "docx_table_block",
                    "source_parser": "docx_parser",
                },
            )
        )
        source_index += 1
        index += 1

    return elements


def document_to_text(document_path: str) -> str:
    """把 DOCX 文档转成可读纯文本摘要。"""

    elements = parse_docx_elements_from_document(document_path)
    return "\n\n".join(element.text for element in elements if element.text.strip()).strip()


def load_python_docx():
    try:
        import docx
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "python-docx is required for DOCX parsing. Install it with `pip install python-docx`."
        ) from exc
    return docx


def iter_block_items(document):
    """按 Word 原生顺序遍历 body 里的 paragraph / table。"""

    from docx.document import Document as _Document
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table, _Cell
    from docx.text.paragraph import Paragraph

    if isinstance(document, _Document):
        parent_elm = document.element.body
        parent = document
    elif isinstance(document, _Cell):
        parent_elm = document._tc
        parent = document
    else:
        raise ValueError("Unsupported parent type for iter_block_items")

    for child in parent_elm.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, parent)
        elif isinstance(child, CT_Tbl):
            yield Table(child, parent)


def is_docx_paragraph(block_item) -> bool:
    from docx.text.paragraph import Paragraph

    return isinstance(block_item, Paragraph)


def normalize_docx_paragraph_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def detect_docx_heading_level(paragraph) -> Optional[int]:
    style_name = get_docx_style_name(paragraph).lower()
    style_id = get_docx_style_id(paragraph).lower()

    for source in (style_name, style_id):
        match = re.search(r"heading\s*([1-6])", source)
        if match:
            return int(match.group(1))

        match = re.search(r"标题\s*([1-6])", source)
        if match:
            return int(match.group(1))

    return None


def is_docx_list_paragraph(paragraph) -> bool:
    style_name = get_docx_style_name(paragraph).lower()
    style_id = get_docx_style_id(paragraph).lower()
    if "list" in style_name or "list" in style_id or "列表" in style_name:
        return True

    paragraph_properties = paragraph._p.pPr
    if paragraph_properties is not None and paragraph_properties.numPr is not None:
        return True

    return False


def detect_docx_list_level(paragraph) -> int:
    paragraph_properties = paragraph._p.pPr
    if paragraph_properties is not None and paragraph_properties.numPr is not None:
        ilvl = paragraph_properties.numPr.ilvl
        if ilvl is not None and ilvl.val is not None:
            return int(ilvl.val) + 1

    left_indent = None
    if paragraph.paragraph_format.left_indent is not None:
        left_indent = paragraph.paragraph_format.left_indent.pt
    if left_indent is not None:
        return max(1, int(left_indent // 18) + 1)

    return 1


def format_docx_list_line(text: str, level: int) -> str:
    indent = "  " * max(level - 1, 0)
    return f"{indent}- {text}"


def get_docx_style_name(paragraph) -> str:
    style = getattr(paragraph, "style", None)
    return getattr(style, "name", "") or ""


def get_docx_style_id(paragraph) -> str:
    style = getattr(paragraph, "style", None)
    return getattr(style, "style_id", "") or ""


def extract_docx_table_rows(table) -> list[list[str]]:
    rows: list[list[str]] = []
    max_columns = 0

    for row in table.rows:
        values = [normalize_docx_paragraph_text(cell.text) for cell in row.cells]
        if not any(values):
            continue
        max_columns = max(max_columns, len(values))
        rows.append(values)

    if max_columns == 0:
        return []

    return [row + [""] * (max_columns - len(row)) for row in rows]


def detect_docx_table_header(rows: list[list[str]]) -> bool:
    if len(rows) < 2:
        return False

    first_row = rows[0]
    second_row = rows[1]

    score = 0
    if any(cell for cell in first_row):
        score += 1
    if not any(is_probably_numeric(cell) for cell in first_row) and any(
        is_probably_numeric(cell) for cell in second_row
    ):
        score += 1
    if first_row != second_row:
        score += 1

    return score >= 2


def format_docx_table_as_markdown(rows: list[list[str]], has_header: bool) -> str:
    lines: list[str] = []
    start_row_index = 1 if has_header else 0

    if has_header:
        lines.append(format_markdown_table_row(rows[0]))
        lines.append(format_markdown_table_separator(len(rows[0])))

    for row in rows[start_row_index:]:
        lines.append(format_markdown_table_row(row))

    if not lines and rows:
        lines.append(format_markdown_table_row(rows[0]))

    return "\n".join(lines).strip()


def format_markdown_table_row(cells: list[str]) -> str:
    return f"| {' | '.join(escape_markdown_table_cell(cell) for cell in cells)} |"


def format_markdown_table_separator(column_count: int) -> str:
    return f"| {' | '.join(['---'] * column_count)} |"


def escape_markdown_table_cell(cell: str) -> str:
    return cell.replace("\n", " ").replace("|", "\\|").strip()


def is_probably_numeric(value: str) -> bool:
    if not value:
        return False
    stripped_value = value.replace(",", "").replace("%", "")
    try:
        float(stripped_value)
        return True
    except ValueError:
        return False


def column_index_to_name(index: int) -> str:
    if index < 0:
        return "A"

    result = ""
    current = index + 1
    while current > 0:
        current, remainder = divmod(current - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result
