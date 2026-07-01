import csv
import io
from typing import Optional

from app.services.document_splitter.models import DocumentElement
from app.services.document_splitter.normalizer import normalize_document_text


def parse_csv_elements(text: str, file_type: str = "csv") -> list[DocumentElement]:
    """把 CSV 文本解析成 table DocumentElement 列表。"""

    normalized_text = normalize_document_text(text).strip()
    if not normalized_text:
        return []

    dialect = detect_csv_dialect(normalized_text)
    rows = read_csv_rows(normalized_text, dialect)
    table_regions = split_csv_table_regions(rows)

    elements: list[DocumentElement] = []
    source_index = 0

    for region_index, (region_start_row, region_rows) in enumerate(table_regions):
        region_row_count = len(region_rows)
        has_header = detect_csv_header(normalized_text, region_rows)
        normalized_rows = normalize_csv_rows(region_rows)
        column_count = len(normalized_rows[0]) if normalized_rows else 0
        if column_count == 0:
            continue

        content = format_csv_region_as_markdown_table(normalized_rows, has_header)
        header_values = normalized_rows[0] if has_header else []
        data_row_start = region_start_row + (1 if has_header else 0)
        data_row_end = region_start_row + region_row_count - 1

        metadata = {
            "file_type": file_type,
            "block_type": "table",
            "splitter": "csv_table_block",
            "source_parser": "csv_parser",
            "delimiter": dialect.delimiter,
            "has_header": has_header,
            "header_row_count": 1 if has_header else 0,
            "header_values": header_values,
            "column_count": column_count,
            "col_start": "A",
            "col_end": column_index_to_name(column_count - 1),
            "table_region_index": region_index,
            "table_format": "csv_markdown",
        }
        if has_header:
            metadata["header_row"] = region_start_row
        if region_row_count > (1 if has_header else 0):
            metadata["row_start"] = data_row_start
            metadata["row_end"] = data_row_end
            metadata["row_count"] = data_row_end - data_row_start + 1

        elements.append(
            DocumentElement(
                source_index=source_index,
                element_type="table",
                text=content,
                row_start=metadata.get("row_start"),
                row_end=metadata.get("row_end"),
                col_start=metadata.get("col_start"),
                col_end=metadata.get("col_end"),
                metadata=metadata,
            )
        )
        source_index += 1

    return elements


def detect_csv_dialect(text: str) -> csv.Dialect:
    """检测 CSV 分隔符。"""

    sample = text[:4096]
    sniffer = csv.Sniffer()
    try:
        return sniffer.sniff(sample, delimiters=",;\t|")
    except csv.Error:
        return csv.get_dialect("excel")


def read_csv_rows(text: str, dialect: csv.Dialect) -> list[list[str]]:
    """按 dialect 读取 CSV 行。"""

    reader = csv.reader(io.StringIO(text), dialect)
    return [list(row) for row in reader]


def split_csv_table_regions(rows: list[list[str]]) -> list[tuple[int, list[list[str]]]]:
    """按空行拆出多个 table region。"""

    regions: list[tuple[int, list[list[str]]]] = []
    current_region: list[list[str]] = []
    current_region_start: Optional[int] = None

    for row_index, row in enumerate(rows, start=1):
        if is_blank_csv_row(row):
            if current_region:
                regions.append((current_region_start or row_index, current_region))
                current_region = []
                current_region_start = None
            continue
        if current_region_start is None:
            current_region_start = row_index
        current_region.append(row)

    if current_region:
        regions.append((current_region_start or 1, current_region))

    return regions


def detect_csv_header(full_text: str, rows: list[list[str]]) -> bool:
    """检测 CSV region 是否有 header。"""

    if len(rows) < 2:
        return False

    sniffer_result = detect_csv_header_with_sniffer(full_text)
    if sniffer_result is not None:
        return sniffer_result

    first_row = [cell.strip() for cell in rows[0]]
    second_row = [cell.strip() for cell in rows[1]]

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


def detect_csv_header_with_sniffer(text: str) -> Optional[bool]:
    sample = text[:4096]
    sniffer = csv.Sniffer()
    try:
        return sniffer.has_header(sample)
    except csv.Error:
        return None


def normalize_csv_rows(rows: list[list[str]]) -> list[list[str]]:
    """统一列数，避免后续组表错位。"""

    max_columns = max((len(row) for row in rows), default=0)
    return [
        [normalize_csv_cell(cell) for cell in row] + [""] * (max_columns - len(row))
        for row in rows
    ]


def format_csv_region_as_markdown_table(rows: list[list[str]], has_header: bool) -> str:
    """把 CSV region 格式化成 Markdown table 文本。"""

    if not rows:
        return ""

    lines: list[str] = []
    start_row_index = 1 if has_header else 0

    if has_header:
        lines.append(format_markdown_table_row(rows[0]))
        lines.append(format_markdown_table_separator(len(rows[0])))

    for row in rows[start_row_index:]:
        lines.append(format_markdown_table_row(row))

    if not lines:
        lines.append(format_markdown_table_row(rows[0]))

    return "\n".join(lines).strip()


def format_markdown_table_row(cells: list[str]) -> str:
    escaped_cells = [escape_markdown_table_cell(cell) for cell in cells]
    return f"| {' | '.join(escaped_cells)} |"


def format_markdown_table_separator(column_count: int) -> str:
    return f"| {' | '.join(['---'] * column_count)} |"


def escape_markdown_table_cell(cell: str) -> str:
    return cell.replace("\n", " ").replace("|", "\\|").strip()


def normalize_csv_cell(cell: str) -> str:
    return " ".join(cell.strip().split())


def is_blank_csv_row(row: list[str]) -> bool:
    return not any(cell.strip() for cell in row)


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
    """把 0-based 列号转成 Excel 风格列名。"""

    if index < 0:
        return "A"

    result = ""
    current = index + 1
    while current > 0:
        current, remainder = divmod(current - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result
