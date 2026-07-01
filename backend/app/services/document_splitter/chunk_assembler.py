import re
from typing import Optional

from app.services.document_splitter.models import Block, ChunkData
from app.services.document_splitter.normalizer import normalize_text


def validate_splitter_options(
    target_chunk_size: int,
    max_chunk_size: int,
    chunk_overlap: int,
) -> None:
    """校验 chunk 参数，避免 target / max / overlap 配置冲突。"""

    if target_chunk_size <= 0:
        raise ValueError("target_chunk_size must be greater than 0")
    if max_chunk_size <= 0:
        raise ValueError("max_chunk_size must be greater than 0")
    if target_chunk_size > max_chunk_size:
        raise ValueError("target_chunk_size must be smaller than or equal to max_chunk_size")
    if chunk_overlap < 0:
        raise ValueError("chunk_overlap must be greater than or equal to 0")
    if chunk_overlap >= max_chunk_size:
        raise ValueError("chunk_overlap must be smaller than max_chunk_size")


def assemble_chunks(
    blocks: list[Block],
    *,
    target_chunk_size: int,
    max_chunk_size: int,
    chunk_overlap: int,
) -> list[ChunkData]:
    """把 Block 组装成接近 target_chunk_size、但不超过 max_chunk_size 的 ChunkData。"""

    prepared_blocks = prepare_blocks_for_packing(blocks, max_chunk_size, chunk_overlap)

    chunks: list[ChunkData] = []
    current_blocks: list[Block] = []
    current_length = 0
    current_section_key: Optional[int] = None

    for block in prepared_blocks:
        content = normalize_text(block.content)
        if not content:
            continue

        if current_blocks and should_flush_on_block_boundary(current_blocks[-1], block):
            flush_current_chunk(
                chunks,
                current_blocks,
                target_chunk_size,
                max_chunk_size,
                chunk_overlap,
            )
            current_blocks = []
            current_length = 0
            current_section_key = None

        section_key = block.metadata.get("section_index")
        if current_blocks and section_key != current_section_key:
            flush_current_chunk(
                chunks,
                current_blocks,
                target_chunk_size,
                max_chunk_size,
                chunk_overlap,
            )
            current_blocks = []
            current_length = 0
            current_section_key = None

        separator_length = 2 if current_blocks else 0
        next_length = current_length + separator_length + len(content)
        should_flush_at_target = (
            current_blocks
            and current_length >= target_chunk_size
            and next_length > target_chunk_size
        )
        should_flush_at_max = current_blocks and next_length > max_chunk_size

        if should_flush_at_target or should_flush_at_max:
            flush_current_chunk(
                chunks,
                current_blocks,
                target_chunk_size,
                max_chunk_size,
                chunk_overlap,
            )
            current_blocks = build_semantic_overlap_blocks(current_blocks, chunk_overlap)
            current_length = calculate_blocks_length(current_blocks)
            current_section_key = current_blocks[0].metadata.get("section_index") if current_blocks else None
            separator_length = 2 if current_blocks else 0

        current_blocks.append(
            Block(
                block_type=block.block_type,
                content=content,
                metadata=block.metadata,
            )
        )
        current_length += separator_length + len(content)
        current_section_key = section_key

    flush_current_chunk(
        chunks,
        current_blocks,
        target_chunk_size,
        max_chunk_size,
        chunk_overlap,
    )
    return chunks


def should_flush_on_block_boundary(prev_block: Block, next_block: Block) -> bool:
    """判断不同 block 类型交界处是否应该强制断开 chunk。"""

    prev_type = prev_block.block_type
    next_type = next_block.block_type

    if prev_type == "heading" or next_type == "heading":
        return False

    if prev_type == "table" and next_type == "table":
        return True

    if (prev_type == "table" and next_type != "table") or (
        next_type == "table" and prev_type != "table"
    ):
        return True

    if (prev_type == "code" and next_type != "code") or (
        next_type == "code" and prev_type != "code"
    ):
        return True

    return False


def prepare_blocks_for_packing(
    blocks: list[Block],
    max_chunk_size: int,
    chunk_overlap: int,
) -> list[Block]:
    """确保进入 pack 流程的每个 Block 都不会明显超过 max_chunk_size。"""

    prepared_blocks: list[Block] = []
    for block in blocks:
        if (
            block.block_type == "heading"
            and block.metadata.get("heading_path") == block.metadata.get("section_heading_path")
        ):
            continue
        prepared_blocks.extend(
            split_block_for_packing(
                block,
                max_chunk_size=max_chunk_size,
                chunk_overlap=chunk_overlap,
            )
        )
    return prepared_blocks


def split_block_for_packing(
    block: Block,
    *,
    max_chunk_size: int,
    chunk_overlap: int,
) -> list[Block]:
    """按 block 类型选择切分策略。"""

    content = normalize_text(block.content)
    normalized_block = Block(
        block_type=block.block_type,
        content=content,
        metadata=block.metadata,
    )
    if len(content) <= max_chunk_size:
        return [normalized_block]

    if normalized_block.block_type == "table":
        return split_table_block(normalized_block, max_chunk_size, chunk_overlap)

    if normalized_block.block_type == "code":
        return split_code_block(normalized_block, max_chunk_size, chunk_overlap)

    if normalized_block.block_type == "list":
        return split_list_block(normalized_block, max_chunk_size, chunk_overlap)

    return split_text_block(normalized_block, max_chunk_size, chunk_overlap)


def split_text_block(
    block: Block,
    max_chunk_size: int,
    chunk_overlap: int,
) -> list[Block]:
    """段落类文本优先按句子切，最后才固定窗口兜底。"""

    sentences = split_sentences(block.content)
    if len(sentences) > 1:
        return [
            Block(
                block_type=block.block_type,
                content=sentence,
                metadata={
                    **block.metadata,
                    "splitter": f"{block.metadata.get('splitter', 'text')}_sentence",
                },
            )
            for sentence in sentences
            if sentence.strip()
        ]

    return split_block_by_fixed_window(block, max_chunk_size, chunk_overlap)


def split_list_block(
    block: Block,
    max_chunk_size: int,
    chunk_overlap: int,
) -> list[Block]:
    """列表优先按 item 切，再退化到句子或固定窗口。"""

    items = split_list_items(block.content)
    if len(items) > 1:
        result: list[Block] = []
        for item in items:
            item_block = Block(
                block_type="list",
                content=item,
                metadata={
                    **block.metadata,
                    "splitter": f"{block.metadata.get('splitter', 'list')}_item",
                },
            )
            if len(item) <= max_chunk_size:
                result.append(item_block)
            else:
                result.extend(split_text_block(item_block, max_chunk_size, chunk_overlap))
        return result

    return split_text_block(block, max_chunk_size, chunk_overlap)


def split_table_block(
    block: Block,
    max_chunk_size: int,
    chunk_overlap: int,
) -> list[Block]:
    """表格按行切，并尽量在每个 chunk 开头保留表头。"""

    lines = [line.rstrip() for line in block.content.splitlines() if line.strip()]
    if not lines:
        return []

    header_lines: list[str] = []
    data_lines = lines
    if len(lines) >= 2 and is_markdown_table_separator(lines[1].strip()):
        header_lines = lines[:2]
        data_lines = lines[2:]

    base_length = len("\n".join(header_lines)) + (1 if header_lines else 0)
    table_row_start = block.metadata.get("row_start")
    data_entries = build_table_data_entries(data_lines, table_row_start)
    current_data_entries: list[tuple[str, Optional[int]]] = []
    table_blocks: list[Block] = []

    def emit_table_block(entries: list[tuple[str, Optional[int]]]) -> None:
        rows = [row_text for row_text, _row_number in entries]
        if not rows and not header_lines:
            return
        content_lines = [*header_lines, *rows]
        metadata = {
            **block.metadata,
            "splitter": f"{block.metadata.get('splitter', 'table')}_rows",
        }
        row_numbers = [
            row_number
            for _row_text, row_number in entries
            if row_number is not None
        ]
        if row_numbers:
            metadata["row_start"] = min(row_numbers)
            metadata["row_end"] = max(row_numbers)
            metadata["row_count"] = len(set(row_numbers))
        if header_lines:
            metadata["header_retained"] = True

        table_blocks.append(
            Block(
                block_type="table",
                content="\n".join(content_lines).strip(),
                metadata=metadata,
            )
        )

    for row_entry in data_entries:
        candidate_entries = [*current_data_entries, row_entry]
        candidate_rows = [row_text for row_text, _row_number in candidate_entries]
        candidate_text = "\n".join([*header_lines, *candidate_rows]).strip()

        if current_data_entries and len(candidate_text) > max_chunk_size:
            emit_table_block(current_data_entries)
            overlap_entries = build_table_overlap_entries(
                current_data_entries,
                header_lines,
                chunk_overlap,
            )
            current_data_entries = [*overlap_entries, row_entry]
            candidate_rows = [row_text for row_text, _row_number in current_data_entries]
            candidate_text = "\n".join([*header_lines, *candidate_rows]).strip()
            if len(candidate_text) <= max_chunk_size:
                continue

        if len(candidate_text) <= max_chunk_size:
            current_data_entries = candidate_entries
            continue

        if current_data_entries:
            emit_table_block(current_data_entries)
            current_data_entries = []

        available_size = max(
            1,
            max_chunk_size - base_length,
        )
        row_fragments = split_text_to_windows(row_entry[0], available_size, chunk_overlap)
        for fragment in row_fragments:
            emit_table_block([(fragment, row_entry[1])])

    if current_data_entries:
        emit_table_block(current_data_entries)

    return table_blocks or [block]


def split_code_block(
    block: Block,
    max_chunk_size: int,
    chunk_overlap: int,
) -> list[Block]:
    """代码块按行切，并保留 fenced code block 的包裹结构。"""

    lines = block.content.splitlines()
    if not lines:
        return []

    opening_fence = lines[0] if lines[0].strip().startswith("```") else None
    closing_fence = lines[-1] if lines[-1].strip().startswith("```") and len(lines) > 1 else None
    body_lines = lines[1:-1] if opening_fence and closing_fence else lines

    code_blocks: list[Block] = []
    current_body_lines: list[str] = []
    shell_length = calculate_code_shell_length(opening_fence, closing_fence)

    def emit_code_block(body: list[str]) -> None:
        if not body:
            return
        content_lines: list[str] = []
        if opening_fence:
            content_lines.append(opening_fence)
        content_lines.extend(body)
        if closing_fence:
            content_lines.append(closing_fence)
        code_blocks.append(
            Block(
                block_type="code",
                content="\n".join(content_lines).strip(),
                metadata={
                    **block.metadata,
                    "splitter": f"{block.metadata.get('splitter', 'code')}_lines",
                },
            )
        )

    for line in body_lines:
        candidate_body = [*current_body_lines, line]
        candidate_text = build_code_chunk_text(candidate_body, opening_fence, closing_fence)

        if current_body_lines and len(candidate_text) > max_chunk_size:
            emit_code_block(current_body_lines)
            overlap_lines = build_code_overlap_lines(current_body_lines, chunk_overlap, shell_length)
            current_body_lines = [*overlap_lines, line]
            candidate_text = build_code_chunk_text(current_body_lines, opening_fence, closing_fence)
            if len(candidate_text) <= max_chunk_size:
                continue

        if len(candidate_text) <= max_chunk_size:
            current_body_lines = candidate_body
            continue

        if current_body_lines:
            emit_code_block(current_body_lines)
            current_body_lines = []

        available_size = max(1, max_chunk_size - shell_length)
        line_fragments = split_text_to_windows(line, available_size, chunk_overlap)
        for fragment in line_fragments:
            emit_code_block([fragment])

    if current_body_lines:
        emit_code_block(current_body_lines)

    return code_blocks or [block]


def split_block_by_fixed_window(
    block: Block,
    max_chunk_size: int,
    chunk_overlap: int,
) -> list[Block]:
    """最后兜底的固定窗口切分，但尽量对齐到单词 / 标点边界。"""

    windows = split_text_to_windows(block.content, max_chunk_size, chunk_overlap)
    return [
        Block(
            block_type=block.block_type,
            content=window,
            metadata={
                **block.metadata,
                "splitter": f"{block.metadata.get('splitter', 'text')}_fixed_window",
            },
        )
        for window in windows
        if window.strip()
    ]


def flush_current_chunk(
    chunks: list[ChunkData],
    blocks: list[Block],
    target_chunk_size: int,
    max_chunk_size: int,
    chunk_overlap: int,
) -> None:
    parts = [block.content for block in blocks if block.content.strip()]
    metadata_items = [block.metadata for block in blocks if block.content.strip()]
    content = "\n\n".join(parts).strip()
    if not content:
        return

    content = prepend_heading_prefix(content, metadata_items)

    chunks.append(
        ChunkData(
            content=content,
            metadata=merge_metadata(
                metadata_items,
                target_chunk_size,
                max_chunk_size,
                chunk_overlap,
            ),
        )
    )


def merge_metadata(
    metadata_items: list[dict[str, object]],
    target_chunk_size: int,
    max_chunk_size: int,
    chunk_overlap: int,
) -> dict[str, object]:
    """合并多个 Block 的 metadata。"""

    if not metadata_items:
        return {
            "target_chunk_size": target_chunk_size,
            "max_chunk_size": max_chunk_size,
            "chunk_overlap": chunk_overlap,
            "splitter": "unknown",
        }

    first = metadata_items[0]
    merged: dict[str, object] = {
        "file_type": first.get("file_type"),
        "splitter": first.get("splitter", "mixed"),
        "target_chunk_size": target_chunk_size,
        "max_chunk_size": max_chunk_size,
        "chunk_overlap": chunk_overlap,
    }

    heading_paths = []
    seen_heading_paths = set()
    for item in metadata_items:
        heading_path = item.get("heading_path")
        if not heading_path:
            continue
        heading_key = tuple(heading_path)
        if heading_key not in seen_heading_paths:
            seen_heading_paths.add(heading_key)
            heading_paths.append(list(heading_key))

    if len(heading_paths) == 1:
        merged["heading_path"] = heading_paths[0]
    elif heading_paths:
        merged["heading_paths"] = heading_paths

    block_types = []
    seen_block_types = set()
    for item in metadata_items:
        block_type = item.get("block_type")
        if block_type and block_type not in seen_block_types:
            seen_block_types.add(block_type)
            block_types.append(block_type)

    if len(block_types) == 1:
        merged["block_type"] = block_types[0]
    elif block_types:
        merged["block_types"] = block_types

    for key in ("page", "page_start", "paragraph_start", "row_start"):
        values = [item.get(key) for item in metadata_items if item.get(key) is not None]
        if values:
            merged[key] = min(values)

    for key in ("page_end", "paragraph_end", "row_end"):
        values = [item.get(key) for item in metadata_items if item.get(key) is not None]
        if values:
            merged[key] = max(values)

    row_counts = [item.get("row_count") for item in metadata_items if item.get("row_count") is not None]
    if row_counts:
        merged["row_count"] = max(row_counts)

    for key in (
        "sheet_name",
        "sheet_index",
        "sheet_used_range",
        "header_row",
        "col_start",
        "col_end",
        "column_count",
        "delimiter",
        "has_header",
        "table_region_index",
    ):
        values = [item.get(key) for item in metadata_items if item.get(key) is not None]
        if values:
            merged[key] = values[0]

    return merged


def split_sentences(text: str) -> list[str]:
    """中英文混合的轻量分句。"""

    parts = re.split(r"(?<=[。！？；.!?;])\s*", text)
    return [part.strip() for part in parts if part.strip()]


def split_list_items(text: str) -> list[str]:
    """按 Markdown list item 拆分列表块。"""

    items: list[list[str]] = []
    current_item: list[str] = []

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^([-*+]|\d+\.)\s+", stripped):
            if current_item:
                items.append(current_item)
            current_item = [line]
            continue
        if current_item and (line.startswith("  ") or line.startswith("\t")):
            current_item.append(line)
            continue
        if current_item:
            current_item.append(line)

    if current_item:
        items.append(current_item)

    return ["\n".join(item).strip() for item in items if any(part.strip() for part in item)]


def build_semantic_overlap_blocks(
    blocks: list[Block],
    chunk_overlap: int,
) -> list[Block]:
    """从上一 chunk 末尾抽取语义级 overlap，避免半词 / 半句开头。"""

    if chunk_overlap <= 0 or not blocks:
        return []

    overlap_blocks: list[Block] = []
    total_length = 0

    for block in reversed(blocks):
        semantic_units = build_overlap_units_from_block(block)
        for unit in reversed(semantic_units):
            unit_length = len(unit.content)
            if overlap_blocks and total_length + unit_length > chunk_overlap:
                return list(reversed(overlap_blocks))
            if not overlap_blocks and unit_length > chunk_overlap:
                return []
            overlap_blocks.append(unit)
            total_length += unit_length + 2

    return list(reversed(overlap_blocks))


def build_overlap_units_from_block(block: Block) -> list[Block]:
    """把一个 block 拆成适合 overlap 的语义单元。"""

    if block.block_type == "heading":
        return []

    if block.block_type == "paragraph":
        sentences = split_sentences(block.content)
        if len(sentences) > 1:
            return [
                Block(
                    block_type="paragraph",
                    content=sentence,
                    metadata={
                        **block.metadata,
                        "splitter": f"{block.metadata.get('splitter', 'paragraph')}_overlap",
                    },
                )
                for sentence in sentences
                if sentence.strip()
            ]
        return [block]

    if block.block_type == "list":
        items = split_list_items(block.content)
        if items:
            return [
                Block(
                    block_type="list",
                    content=item,
                    metadata={
                        **block.metadata,
                        "splitter": f"{block.metadata.get('splitter', 'list')}_overlap",
                    },
                )
                for item in items
                if item.strip()
            ]

    return []


def build_table_overlap_entries(
    rows: list[tuple[str, Optional[int]]],
    header_lines: list[str],
    chunk_overlap: int,
) -> list[tuple[str, Optional[int]]]:
    """表格 overlap 只复制完整数据行。"""

    if chunk_overlap <= 0:
        return []

    overlap_rows: list[tuple[str, Optional[int]]] = []
    total = len("\n".join(header_lines)) if header_lines else 0

    for row_text, row_number in reversed(rows):
        row_length = len(row_text) + 1
        if overlap_rows and total + row_length > chunk_overlap:
            break
        if not overlap_rows and total + row_length > chunk_overlap:
            return []
        overlap_rows.append((row_text, row_number))
        total += row_length

    return list(reversed(overlap_rows))


def build_table_data_entries(
    rows: list[str],
    starting_row_number: Optional[int],
) -> list[tuple[str, Optional[int]]]:
    """给表格数据行补上绝对行号。"""

    entries: list[tuple[str, Optional[int]]] = []
    for index, row_text in enumerate(rows):
        row_number = None if starting_row_number is None else starting_row_number + index
        entries.append((row_text, row_number))
    return entries


def build_code_overlap_lines(
    lines: list[str],
    chunk_overlap: int,
    shell_length: int,
) -> list[str]:
    """代码 overlap 只复制完整代码行。"""

    if chunk_overlap <= 0:
        return []

    overlap_lines: list[str] = []
    total = shell_length

    for line in reversed(lines):
        line_length = len(line) + 1
        if overlap_lines and total + line_length > chunk_overlap:
            break
        if not overlap_lines and total + line_length > chunk_overlap:
            return []
        overlap_lines.append(line)
        total += line_length

    return list(reversed(overlap_lines))


def split_text_to_windows(
    text: str,
    max_chunk_size: int,
    chunk_overlap: int,
) -> list[str]:
    """固定窗口兜底，但对齐到可读边界，避免半词开头。"""

    if not text.strip():
        return []

    chunks: list[str] = []
    start = 0
    text_length = len(text)

    while start < text_length:
        ideal_end = min(start + max_chunk_size, text_length)
        end = choose_window_end(text, start, ideal_end)
        if end <= start:
            end = min(start + max_chunk_size, text_length)

        chunk_text = text[start:end].strip()
        if chunk_text:
            chunks.append(chunk_text)

        if end >= text_length:
            break

        next_start = max(0, end - chunk_overlap)
        next_start = align_window_start(text, next_start)
        if next_start <= start:
            next_start = end
        start = next_start

    return chunks


def choose_window_end(text: str, start: int, ideal_end: int) -> int:
    """优先在窗口末尾附近找边界点。"""

    if ideal_end >= len(text):
        return len(text)

    lower_bound = max(start + 1, ideal_end - 80)
    for index in range(ideal_end, lower_bound - 1, -1):
        if is_split_boundary(text, index):
            return index

    upper_bound = min(len(text), ideal_end + 80)
    for index in range(ideal_end + 1, upper_bound + 1):
        if is_split_boundary(text, index):
            return index

    return ideal_end


def align_window_start(text: str, start: int) -> int:
    """把窗口起点对齐到单词或标点边界之后。"""

    length = len(text)
    index = max(0, start)

    while index < length and not is_split_boundary(text, index):
        index += 1

    while index < length and text[index].isspace():
        index += 1

    return index


def is_split_boundary(text: str, index: int) -> bool:
    """判断某个位置是不是适合切分的边界。"""

    if index <= 0 or index >= len(text):
        return True

    left = text[index - 1]
    right = text[index]
    boundary_chars = " \t\n\r,.;:!?，。；：！？、)]}>\"'」』】"

    return left in boundary_chars or right in boundary_chars


def prepend_heading_prefix(
    content: str,
    metadata_items: list[dict[str, object]],
) -> str:
    """给有 heading_path 的 chunk 统一补标题前缀。"""

    if not metadata_items:
        return content

    first = metadata_items[0]
    heading_path = first.get("heading_path") or []
    if not heading_path:
        return content

    file_type = str(first.get("file_type") or "")
    level = max(1, min(len(heading_path), 6))
    heading_text = heading_path[-1]
    heading_line = f"{'#' * level} {heading_text}"

    if content.startswith(heading_line):
        return content

    if file_type == "xlsx" and first.get("sheet_name"):
        heading_line = f"# {first['sheet_name']}"
    elif file_type == "csv" and first.get("sheet_name"):
        heading_line = f"# {first['sheet_name']}"

    return f"{heading_line}\n\n{content}"


def calculate_blocks_length(blocks: list[Block]) -> int:
    """计算若干 block 拼接后的长度。"""

    total = 0
    for index, block in enumerate(blocks):
        total += len(block.content)
        if index > 0:
            total += 2
    return total


def calculate_code_shell_length(
    opening_fence: Optional[str],
    closing_fence: Optional[str],
) -> int:
    """计算 fenced code block 的固定包裹长度。"""

    total = 0
    if opening_fence:
        total += len(opening_fence) + 1
    if closing_fence:
        total += len(closing_fence) + 1
    return total


def build_code_chunk_text(
    body_lines: list[str],
    opening_fence: Optional[str],
    closing_fence: Optional[str],
) -> str:
    """构造带 fence 的代码块文本。"""

    lines: list[str] = []
    if opening_fence:
        lines.append(opening_fence)
    lines.extend(body_lines)
    if closing_fence:
        lines.append(closing_fence)
    return "\n".join(lines).strip()


def is_markdown_table_separator(line: str) -> bool:
    """判断一行是不是 Markdown 表格分隔线。"""

    cells = [cell.strip() for cell in line.strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells if cell)


class DefaultChunkAssembler:
    """Phase 1 的默认 chunk assembler。"""

    def assemble(self, blocks: list[Block], options) -> list[ChunkData]:
        return assemble_chunks(
            blocks,
            target_chunk_size=options.target_chunk_size,
            max_chunk_size=options.max_chunk_size,
            chunk_overlap=options.chunk_overlap,
        )
