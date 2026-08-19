import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional, Union

from app.services.document_splitter.chunk_assembler import (
    assemble_element_chunks,
    is_markdown_table_separator,
    validate_splitter_options,
)
from app.services.document_splitter.models import (
    Block,
    ChunkData,
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_MAX_CHUNK_SIZE,
    DEFAULT_TARGET_CHUNK_SIZE,
    DocumentElement,
    PdfPageText,
    Section,
)
from app.services.document_splitter.splitter import (
    build_document_elements,
    build_document_blocks,
    build_document_sections,
    normalize_splitter_source,
    parse_splitter_source,
)


def build_splitter_regression_snapshot(
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
) -> dict[str, Any]:
    """生成 splitter 全链路快照，便于回归比对。"""

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
    sections = build_document_sections(normalized_source)
    blocks = build_document_blocks(normalized_source)
    chunks = assemble_element_chunks(
        build_document_elements(normalized_source),
        target_chunk_size=resolved_target_chunk_size,
        max_chunk_size=resolved_max_chunk_size,
        chunk_overlap=chunk_overlap,
    )

    return {
        "file_type": normalized_source.file_type,
        "options": {
            "target_chunk_size": resolved_target_chunk_size,
            "max_chunk_size": resolved_max_chunk_size,
            "chunk_overlap": chunk_overlap,
        },
        "elements": [serialize_document_element(element) for element in normalized_source.elements or []],
        "sections": [serialize_section(section) for section in sections],
        "blocks": [serialize_block(block) for block in blocks],
        "chunks": [serialize_chunk(chunk) for chunk in chunks],
    }


def evaluate_splitter_regression_snapshot(
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """对回归快照做轻量质量评估。"""

    elements = snapshot.get("elements", [])
    sections = snapshot.get("sections", [])
    blocks = snapshot.get("blocks", [])
    chunks = snapshot.get("chunks", [])
    max_chunk_size = int(snapshot.get("options", {}).get("max_chunk_size", DEFAULT_MAX_CHUNK_SIZE))

    chunk_lengths = [len(chunk.get("content", "")) for chunk in chunks]
    table_chunks = [chunk for chunk in chunks if is_table_chunk_snapshot(chunk)]
    heading_path_chunks = [
        chunk
        for chunk in chunks
        if chunk.get("metadata", {}).get("heading_path")
    ]
    noisy_chunks = [
        chunk
        for chunk in chunks
        if is_noise_chunk_snapshot(chunk.get("content", ""))
    ]

    metrics = {
        "element_count": len(elements),
        "section_count": len(sections),
        "block_count": len(blocks),
        "chunk_count": len(chunks),
        "avg_chunk_length": round(sum(chunk_lengths) / len(chunk_lengths), 2) if chunk_lengths else 0,
        "min_chunk_length": min(chunk_lengths) if chunk_lengths else 0,
        "max_chunk_length": max(chunk_lengths) if chunk_lengths else 0,
        "oversized_chunk_count": sum(1 for length in chunk_lengths if length > max_chunk_size),
        "suspicious_chunk_start_count": sum(
            1 for chunk in chunks if has_suspicious_chunk_start(chunk.get("content", ""))
        ),
        "noise_chunk_count": len(noisy_chunks),
        "table_fragment_chunk_count": sum(
            1 for chunk in table_chunks if is_table_fragment_chunk(chunk.get("content", ""))
        ),
        "element_source_parser_coverage_ratio": calculate_ratio(
            sum(1 for element in elements if element.get("metadata", {}).get("source_parser")),
            len(elements),
        ),
        "block_heading_path_coverage_ratio": calculate_ratio(
            sum(1 for block in blocks if "heading_path" in block.get("metadata", {})),
            len(blocks),
        ),
        "heading_prefix_applicable_chunk_count": len(heading_path_chunks),
        "heading_prefix_ratio": calculate_ratio(
            sum(1 for chunk in heading_path_chunks if has_heading_prefix(chunk.get("content", ""))),
            len(heading_path_chunks),
        ),
        "table_chunk_count": len(table_chunks),
        "table_header_retention_ratio": calculate_ratio(
            sum(1 for chunk in table_chunks if not is_table_fragment_chunk(chunk.get("content", ""))),
            len(table_chunks),
        ),
    }
    return metrics


def write_regression_artifact(path: Union[str, Path], data: dict[str, Any]) -> None:
    """把回归快照或评估结果写成格式化 JSON。"""

    artifact_path = Path(path)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def serialize_document_element(element: DocumentElement) -> dict[str, Any]:
    return clean_snapshot_value(asdict(element))


def serialize_section(section: Section) -> dict[str, Any]:
    return clean_snapshot_value(asdict(section))


def serialize_block(block: Block) -> dict[str, Any]:
    return clean_snapshot_value(asdict(block))


def serialize_chunk(chunk: ChunkData) -> dict[str, Any]:
    return clean_snapshot_value(asdict(chunk))


def clean_snapshot_value(value: Any) -> Any:
    """去掉 None，保证快照更稳定、更易读。"""

    if isinstance(value, dict):
        cleaned_items: dict[str, Any] = {}
        for key in sorted(value.keys()):
            cleaned = clean_snapshot_value(value[key])
            if cleaned is None:
                continue
            cleaned_items[key] = cleaned
        return cleaned_items

    if isinstance(value, list):
        return [clean_snapshot_value(item) for item in value]

    return value


def calculate_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def has_heading_prefix(content: str) -> bool:
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        return stripped.startswith("#")
    return False


def is_table_chunk_snapshot(chunk: dict[str, Any]) -> bool:
    metadata = chunk.get("metadata", {})
    if metadata.get("block_type") == "table":
        return True
    block_types = metadata.get("block_types") or []
    return "table" in block_types


def is_table_fragment_chunk(content: str) -> bool:
    lines = normalize_chunk_lines_for_structure_check(content)
    if not lines:
        return False
    if not lines[0].startswith("|"):
        return False
    if len(lines) < 2:
        return True
    return not is_markdown_table_separator(lines[1].strip())


def has_suspicious_chunk_start(content: str) -> bool:
    lines = normalize_chunk_lines_for_structure_check(content)
    if not lines:
        return False

    first_line = lines[0].lstrip()
    if not first_line:
        return False

    if first_line.startswith(("```", "- ", "* ", "+ ", "|")):
        return False

    if first_line[0] in ",.;:!?)]}>，。；：！？、】」』":
        return True

    return bool(re.match(r"^[a-z][A-Za-z0-9_-]{1,20}\b", first_line))


def normalize_chunk_lines_for_structure_check(content: str) -> list[str]:
    """忽略标题前缀后，再检查真正正文。"""

    lines: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#") or stripped.startswith("标题：") or stripped.startswith("工作表："):
            continue
        lines.append(stripped)
    return lines


def is_noise_chunk_snapshot(content: str) -> bool:
    lines = normalize_chunk_lines_for_structure_check(content)
    if not lines:
        return False
    if len(lines) != 1:
        return False

    line = lines[0].strip().lower()
    return bool(
        re.match(r"^page\s+\d+$", line)
        or re.match(r"^\d+\s*/\s*\d+$", line)
        or re.match(r"^第\s*\d+\s*页$", line)
    )
