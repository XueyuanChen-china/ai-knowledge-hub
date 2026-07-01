import statistics
from dataclasses import dataclass
import re
from typing import Optional

from app.services.document_splitter.models import DocumentElement
from app.services.document_splitter.parsers.plain_text_parser import match_heading_pattern


HEADER_ZONE_RATIO = 0.12
FOOTER_ZONE_RATIO = 0.88
LINE_MERGE_Y_TOLERANCE = 4.0
PARAGRAPH_GAP_RATIO = 1.65


@dataclass
class PdfLayoutLine:
    text: str
    page_number: int
    bbox: list[float]
    avg_font_size: float
    column_index: int


@dataclass
class PdfTableRegion:
    page_number: int
    bbox: list[float]
    rows: list[list[str]]


def parse_pdf_layout_elements_from_document(
    pdf_path: str,
    file_type: str = "pdf",
) -> Optional[list[DocumentElement]]:
    """基于 PDF layout 解析 paragraph / heading / table elements。"""

    pdfplumber = load_pdfplumber()
    with pdfplumber.open(pdf_path) as pdf:
        page_layouts = [extract_page_layout(page) for page in pdf.pages]

    if not page_layouts:
        return None

    header_texts, footer_texts = detect_repeated_header_footer_texts(page_layouts)

    page_font_medians = {
        layout["page_number"]: compute_median_font_size(layout["lines"])
        for layout in page_layouts
    }
    elements = build_pdf_layout_elements_from_pages(
        page_layouts,
        page_font_medians=page_font_medians,
        header_texts=header_texts,
        footer_texts=footer_texts,
        file_type=file_type,
    )
    return elements or None


def build_pdf_layout_elements_from_pages(
    page_layouts: list[dict[str, object]],
    *,
    page_font_medians: dict[int, float],
    header_texts: set[str],
    footer_texts: set[str],
    file_type: str,
) -> list[DocumentElement]:
    """按阅读顺序把 PDF layout 转成 elements。"""

    elements: list[DocumentElement] = []
    current_heading_stack: list[str] = []
    paragraph_lines: list[PdfLayoutLine] = []
    source_index = 0
    previous_line: Optional[PdfLayoutLine] = None

    def flush_paragraph() -> None:
        nonlocal source_index
        if not paragraph_lines:
            return

        content = " ".join(line.text for line in paragraph_lines).strip()
        if not content:
            paragraph_lines.clear()
            return

        bbox = merge_line_bboxes(paragraph_lines)
        page_start = min(line.page_number for line in paragraph_lines)
        page_end = max(line.page_number for line in paragraph_lines)
        elements.append(
            DocumentElement(
                source_index=source_index,
                element_type="paragraph",
                text=content,
                page_start=page_start,
                page_end=page_end,
                bbox=bbox,
                metadata={
                    "file_type": file_type,
                    "heading_path": current_heading_stack.copy(),
                    "block_type": "paragraph",
                    "splitter": "pdf_layout_paragraph_block",
                    "source_parser": "pdf_layout_parser",
                    "page_start": page_start,
                    "page_end": page_end,
                    "column_index": paragraph_lines[0].column_index,
                },
            )
        )
        source_index += 1
        paragraph_lines.clear()

    for layout in page_layouts:
        page_number = int(layout["page_number"])
        page_height = float(layout["height"])
        page_width = float(layout["width"])
        median_font_size = page_font_medians.get(page_number, 0.0)
        table_regions: list[PdfTableRegion] = list(layout["tables"])
        table_bboxes = [table.bbox for table in table_regions]
        content_lines = [
            line
            for line in layout["lines"]
            if not is_header_or_footer_line(line, header_texts, footer_texts, page_height)
            and not overlaps_any_bbox(line.bbox, table_bboxes)
        ]

        page_items = build_pdf_page_items(content_lines, table_regions, page_width)
        for item_type, item in page_items:
            if item_type == "table":
                flush_paragraph()
                elements.append(
                    create_pdf_table_element(
                        item,
                        heading_path=current_heading_stack.copy(),
                        file_type=file_type,
                        source_index=source_index,
                        page_width=page_width,
                    )
                )
                source_index += 1
                previous_line = None
                continue

            line = item
            heading_level = detect_pdf_heading_level(line, median_font_size)
            if heading_level is not None:
                flush_paragraph()
                current_heading_stack[:] = current_heading_stack[: max(heading_level - 1, 0)]
                current_heading_stack.append(line.text)
                elements.append(
                    DocumentElement(
                        source_index=source_index,
                        element_type="heading",
                        text=line.text,
                        level=heading_level,
                        page_start=line.page_number,
                        page_end=line.page_number,
                        bbox=line.bbox,
                        metadata={
                            "file_type": file_type,
                            "heading_path": current_heading_stack.copy(),
                            "heading_level": heading_level,
                            "block_type": "heading",
                            "splitter": "pdf_layout_heading_block",
                            "source_parser": "pdf_layout_parser",
                            "page_start": line.page_number,
                            "page_end": line.page_number,
                            "column_index": line.column_index,
                            "font_size": line.avg_font_size,
                        },
                    )
                )
                source_index += 1
                previous_line = line
                continue

            if should_start_new_pdf_paragraph(previous_line, line, median_font_size):
                flush_paragraph()

            paragraph_lines.append(line)
            previous_line = line

    flush_paragraph()
    return elements


def build_pdf_page_items(
    lines: list[PdfLayoutLine],
    tables: list[PdfTableRegion],
    page_width: float,
) -> list[tuple[str, object]]:
    """把单页的 line / table 组合成阅读顺序流。"""

    items: list[tuple[str, object, int, float, float]] = []
    for line in lines:
        items.append(("line", line, line.column_index, line.bbox[1], line.bbox[0]))
    for table in tables:
        items.append(
            (
                "table",
                table,
                infer_bbox_column_index(table.bbox, page_width),
                table.bbox[1],
                table.bbox[0],
            )
        )

    items.sort(key=lambda item: (item[2], item[3], item[4]))
    return [(item_type, payload) for item_type, payload, _column_index, _top, _x0 in items]


def load_pdfplumber():
    try:
        import pdfplumber
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "pdfplumber is required for PDF layout parsing. Install it with `pip install pdfplumber`."
        ) from exc
    return pdfplumber


def extract_page_layout(page) -> dict[str, object]:
    """提取单页 layout 基础信息。"""

    raw_words = page.extract_words(
        x_tolerance=2,
        y_tolerance=3,
        extra_attrs=["size", "fontname"],
        keep_blank_chars=False,
        use_text_flow=False,
    )
    words = [
        {
            **word,
            "page_number": page.page_number,
        }
        for word in raw_words
    ]
    tables = extract_pdf_tables(page)
    lines = build_pdf_lines_from_words(words, page.width)

    return {
        "page_number": page.page_number,
        "width": float(page.width),
        "height": float(page.height),
        "lines": lines,
        "tables": tables,
    }


def extract_pdf_tables(page) -> list[PdfTableRegion]:
    """抽取 PDF 表格区域。"""

    regions: list[PdfTableRegion] = []
    for table in page.find_tables():
        rows = [
            [normalize_table_cell(cell) for cell in row]
            for row in (table.extract() or [])
        ]
        rows = [row for row in rows if any(cell for cell in row)]
        if not rows:
            continue
        max_columns = max(len(row) for row in rows)
        normalized_rows = [row + [""] * (max_columns - len(row)) for row in rows]
        regions.append(
            PdfTableRegion(
                page_number=page.page_number,
                bbox=[
                    float(table.bbox[0]),
                    float(table.bbox[1]),
                    float(table.bbox[2]),
                    float(table.bbox[3]),
                ],
                rows=normalized_rows,
            )
        )
    return regions


def normalize_table_cell(cell: Optional[str]) -> str:
    if cell is None:
        return ""
    return " ".join(str(cell).strip().split())


def build_pdf_lines_from_words(words: list[dict[str, object]], page_width: float) -> list[PdfLayoutLine]:
    """把词级 layout 信息聚合成行。"""

    if not words:
        return []

    column_mode = detect_page_column_mode(words, page_width)
    column_buckets: dict[int, list[dict[str, object]]] = {}

    for word in words:
        column_index = assign_word_column(word, page_width, column_mode)
        column_buckets.setdefault(column_index, []).append(word)

    lines: list[PdfLayoutLine] = []
    for column_index in sorted(column_buckets):
        column_words = sorted(
            column_buckets[column_index],
            key=lambda word: (float(word["top"]), float(word["x0"])),
        )
        current_words: list[dict[str, object]] = []
        current_top: Optional[float] = None

        for word in column_words:
            word_top = float(word["top"])
            if current_words and current_top is not None and abs(word_top - current_top) > LINE_MERGE_Y_TOLERANCE:
                lines.append(build_line_from_words(current_words, column_index))
                current_words = []
                current_top = None

            current_words.append(word)
            if current_top is None:
                current_top = word_top

        if current_words:
            lines.append(build_line_from_words(current_words, column_index))

    return sorted(lines, key=lambda line: (line.column_index, line.bbox[1], line.bbox[0]))


def detect_page_column_mode(words: list[dict[str, object]], page_width: float) -> str:
    """检测页面更像单栏还是双栏。"""

    left_count = 0
    right_count = 0
    center_count = 0
    midpoint = page_width / 2
    gutter = page_width * 0.12

    for word in words:
        x0 = float(word["x0"])
        x1 = float(word["x1"])
        if x1 <= midpoint - gutter / 2:
            left_count += 1
        elif x0 >= midpoint + gutter / 2:
            right_count += 1
        else:
            center_count += 1

    if left_count >= 12 and right_count >= 12 and center_count <= (left_count + right_count) * 0.35:
        return "two_column"
    return "single_column"


def assign_word_column(word: dict[str, object], page_width: float, column_mode: str) -> int:
    if column_mode != "two_column":
        return 0

    midpoint = page_width / 2
    center_x = (float(word["x0"]) + float(word["x1"])) / 2
    return 0 if center_x < midpoint else 1


def build_line_from_words(words: list[dict[str, object]], column_index: int) -> PdfLayoutLine:
    sorted_words = sorted(words, key=lambda word: float(word["x0"]))
    text = join_line_words(sorted_words)
    bbox = [
        min(float(word["x0"]) for word in sorted_words),
        min(float(word["top"]) for word in sorted_words),
        max(float(word["x1"]) for word in sorted_words),
        max(float(word["bottom"]) for word in sorted_words),
    ]
    font_sizes = [float(word.get("size") or 0.0) for word in sorted_words if word.get("size") is not None]
    avg_font_size = sum(font_sizes) / len(font_sizes) if font_sizes else 0.0

    return PdfLayoutLine(
        text=text,
        page_number=int(sorted_words[0]["page_number"]),
        bbox=bbox,
        avg_font_size=avg_font_size,
        column_index=column_index,
    )


def join_line_words(words: list[dict[str, object]]) -> str:
    parts: list[str] = []
    previous_x1: Optional[float] = None
    for word in words:
        text = str(word.get("text") or "").strip()
        if not text:
            continue
        x0 = float(word["x0"])
        if parts and previous_x1 is not None and x0 - previous_x1 > 1.5:
            parts.append(" ")
        parts.append(text)
        previous_x1 = float(word["x1"])
    return "".join(parts).strip()


def detect_repeated_header_footer_texts(
    page_layouts: list[dict[str, object]],
) -> tuple[set[str], set[str]]:
    """检测重复出现的页眉页脚文本。"""

    header_counts: dict[str, int] = {}
    footer_counts: dict[str, int] = {}

    for layout in page_layouts:
        page_height = float(layout["height"])
        seen_headers: set[str] = set()
        seen_footers: set[str] = set()
        for line in layout["lines"]:
            normalized_text = normalize_header_footer_candidate_text(line.text)
            if not normalized_text:
                continue

            if line.bbox[1] <= page_height * HEADER_ZONE_RATIO:
                seen_headers.add(normalized_text)
            if line.bbox[3] >= page_height * FOOTER_ZONE_RATIO:
                seen_footers.add(normalized_text)

        for text in seen_headers:
            header_counts[text] = header_counts.get(text, 0) + 1
        for text in seen_footers:
            footer_counts[text] = footer_counts.get(text, 0) + 1

    header_texts = {text for text, count in header_counts.items() if count >= 2}
    footer_texts = {text for text, count in footer_counts.items() if count >= 2}
    return header_texts, footer_texts


def normalize_layout_text(text: str) -> str:
    return " ".join(text.strip().split()).lower()


def normalize_header_footer_candidate_text(text: str) -> str:
    """把页眉页脚候选做轻量归一化，保留重复模式而忽略页码变化。"""

    normalized = normalize_layout_text(text)
    if not normalized:
        return ""

    normalized = re.sub(r"\bpage\s+\d+\b", "page <num>", normalized)
    normalized = re.sub(r"\b\d+\s*/\s*\d+\b", "<num>/<num>", normalized)
    normalized = re.sub(r"\b\d+\b", "<num>", normalized)
    return normalized


def is_header_or_footer_line(
    line: PdfLayoutLine,
    header_texts: set[str],
    footer_texts: set[str],
    page_height: float,
) -> bool:
    normalized_text = normalize_header_footer_candidate_text(line.text)
    if not normalized_text:
        return True
    if normalized_text in header_texts and line.bbox[1] <= page_height * HEADER_ZONE_RATIO:
        return True
    if normalized_text in footer_texts and line.bbox[3] >= page_height * FOOTER_ZONE_RATIO:
        return True
    if line.bbox[3] >= page_height * FOOTER_ZONE_RATIO and is_probable_page_marker_text(line.text):
        return True
    return False


def is_probable_page_marker_text(text: str) -> bool:
    normalized = normalize_layout_text(text)
    return bool(
        re.match(r"^page\s+\d+$", normalized)
        or re.match(r"^\d+\s*/\s*\d+$", normalized)
        or re.match(r"^第\s*\d+\s*页$", normalized)
    )


def overlaps_any_bbox(bbox: list[float], bboxes: list[list[float]]) -> bool:
    return any(intersects_bbox(bbox, candidate) for candidate in bboxes)


def intersects_bbox(left: list[float], right: list[float]) -> bool:
    return not (
        left[2] <= right[0]
        or left[0] >= right[2]
        or left[3] <= right[1]
        or left[1] >= right[3]
    )


def detect_pdf_heading_level(line: PdfLayoutLine, median_font_size: float) -> Optional[int]:
    normalized_text = line.text.strip()
    if not normalized_text or len(normalized_text) > 80:
        return None

    pattern_result = match_heading_pattern(normalized_text)
    size_threshold = median_font_size + 1.2 if median_font_size > 0 else line.avg_font_size + 1
    looks_big = line.avg_font_size >= size_threshold and len(normalized_text) <= 40

    if pattern_result is not None:
        level = pattern_result[0]
        if looks_big or len(normalized_text) <= 30:
            return level

    if looks_big and len(normalized_text) <= 30:
        return 1

    return None


def should_start_new_pdf_paragraph(
    previous_line: Optional[PdfLayoutLine],
    current_line: PdfLayoutLine,
    median_font_size: float,
) -> bool:
    if previous_line is None:
        return False
    if current_line.page_number != previous_line.page_number:
        return True
    if current_line.column_index != previous_line.column_index:
        return True

    previous_height = max(previous_line.bbox[3] - previous_line.bbox[1], 1.0)
    current_gap = current_line.bbox[1] - previous_line.bbox[3]
    paragraph_gap = max(previous_height, median_font_size or previous_height) * PARAGRAPH_GAP_RATIO
    return current_gap > paragraph_gap


def merge_line_bboxes(lines: list[PdfLayoutLine]) -> list[float]:
    return [
        min(line.bbox[0] for line in lines),
        min(line.bbox[1] for line in lines),
        max(line.bbox[2] for line in lines),
        max(line.bbox[3] for line in lines),
    ]


def compute_median_font_size(lines: list[PdfLayoutLine]) -> float:
    sizes = [line.avg_font_size for line in lines if line.avg_font_size > 0]
    if not sizes:
        return 0.0
    return float(statistics.median(sizes))


def create_pdf_table_element(
    table: PdfTableRegion,
    *,
    heading_path: list[str],
    file_type: str,
    source_index: int,
    page_width: float,
) -> DocumentElement:
    has_header = detect_pdf_table_header(table.rows)
    content = format_pdf_table_as_markdown(table.rows, has_header)
    row_start = 2 if has_header else 1
    row_end = len(table.rows)
    column_index = infer_bbox_column_index(table.bbox, page_width)
    return DocumentElement(
        source_index=source_index,
        element_type="table",
        text=content,
        page_start=table.page_number,
        page_end=table.page_number,
        row_start=row_start,
        row_end=row_end,
        col_start="A",
        col_end=column_index_to_name(len(table.rows[0]) - 1),
        bbox=table.bbox,
        metadata={
            "file_type": file_type,
            "heading_path": heading_path,
            "block_type": "table",
            "has_header": has_header,
            "header_row_count": 1 if has_header else 0,
            "header_values": table.rows[0] if has_header else [],
            "row_start": row_start,
            "row_end": row_end,
            "row_count": row_end - row_start + 1 if row_end >= row_start else 0,
            "column_count": len(table.rows[0]),
            "col_start": "A",
            "col_end": column_index_to_name(len(table.rows[0]) - 1),
            "page_start": table.page_number,
            "page_end": table.page_number,
            "column_index": column_index,
            "table_format": "pdf_layout_markdown",
            "splitter": "pdf_layout_table_block",
            "source_parser": "pdf_layout_parser",
        },
    )


def infer_bbox_column_index(bbox: list[float], page_width: float) -> int:
    midpoint = page_width / 2
    center_x = (bbox[0] + bbox[2]) / 2
    return 0 if center_x < midpoint else 1


def detect_pdf_table_header(rows: list[list[str]]) -> bool:
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


def format_pdf_table_as_markdown(rows: list[list[str]], has_header: bool) -> str:
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
