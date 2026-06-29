import re
from typing import Any, Optional

from app.services.document_splitter.models import (
    Block,
    ChunkData,
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_MAX_CHUNK_SIZE,
    DEFAULT_TARGET_CHUNK_SIZE,
    PdfPageText,
    Section,
)

DEFAULT_CHUNK_SIZE = DEFAULT_MAX_CHUNK_SIZE


def split_document_text(
    text: str,
    file_type: str,
    *,
    chunk_size: Optional[int] = None,
    target_chunk_size: int = DEFAULT_TARGET_CHUNK_SIZE,
    max_chunk_size: int = DEFAULT_MAX_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    pdf_pages: Optional[list[PdfPageText]] = None,
) -> list[ChunkData]:
    """根据文件类型选择合适的混合切分策略。"""

    resolved_max_chunk_size = chunk_size or max_chunk_size
    resolved_target_chunk_size = min(target_chunk_size, resolved_max_chunk_size)

    validate_splitter_options(
        resolved_target_chunk_size,
        resolved_max_chunk_size,
        chunk_overlap,
    )
    normalized_file_type = file_type.lower()

    if normalized_file_type == "md":
        sections = split_markdown_sections(text)
    elif normalized_file_type == "pdf" and pdf_pages is not None:
        sections = split_pdf_sections(pdf_pages)
    else:
        sections = split_plain_text_sections(text, normalized_file_type)

    blocks = flatten_sections_to_blocks(sections)

    return assemble_chunks(
        blocks,
        target_chunk_size=resolved_target_chunk_size,
        max_chunk_size=resolved_max_chunk_size,
        chunk_overlap=chunk_overlap,
    )


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


def split_markdown_sections(text: str) -> list[Section]:
    """按 Markdown 标题结构切出 Section，再在 Section 内解析 Block。

    第一版规则：
    - 如果文档里存在 ##，则 ## 作为主 section 边界，# 作为文档标题上下文
    - 如果文档里不存在 ##，则退化成按 # 切主 section
    - ### 及以下默认只更新 heading_path，不强制切 section
    """

    sections: list[Section] = []
    section_boundary_level = detect_markdown_section_boundary_level(text)
    heading_stack: list[str] = []
    current_lines: list[str] = []
    current_heading_path: list[str] = []
    current_heading_context_path: list[str] = []
    current_level = 0
    in_code_block = False

    for line in text.splitlines():
        stripped = line.strip()
        heading_match = None if in_code_block else re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading_match:
            heading_level = len(heading_match.group(1))
            heading = heading_match.group(2).strip()
            next_heading_stack = heading_stack[: heading_level - 1]
            next_heading_stack.append(heading)

            should_start_new_section = (
                heading_level == section_boundary_level
                and bool(current_lines)
            )
            if should_start_new_section:
                if not is_only_heading_context_section(
                    current_lines,
                    section_boundary_level,
                ):
                    append_markdown_section(
                        sections,
                        current_lines,
                        current_heading_path,
                        current_level,
                        current_heading_context_path,
                    )
                current_lines = []

            if heading_level == section_boundary_level or not current_heading_path:
                current_heading_context_path = next_heading_stack[:-1]
                current_heading_path = next_heading_stack.copy()
                current_level = heading_level

            heading_stack = next_heading_stack
            current_lines.append(line)
            continue

        current_lines.append(line)
        if stripped.startswith("```"):
            in_code_block = not in_code_block

    append_markdown_section(
        sections,
        current_lines,
        current_heading_path,
        current_level,
        current_heading_context_path,
    )

    if not sections:
        return split_plain_text_sections(text, "md")

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


def split_plain_text_sections(text: str, file_type: str = "txt") -> list[Section]:
    """把纯文本切成 Section。

    策略分两层：
    - 如果检测到多个可靠标题，按标题切 Section
    - 否则退回按空行切 paragraph，全部放进一个 Section
    """

    lines = text.splitlines()
    heading_candidates = detect_plain_text_headings(lines)
    if len(heading_candidates) >= 2:
        return build_plain_text_heading_sections(lines, heading_candidates, file_type)

    return build_plain_text_paragraph_section(text, file_type)


def detect_plain_text_headings(lines: list[str]) -> list[tuple[int, int, str]]:
    """检测纯文本中的可靠标题。

    返回值为 `(line_index, heading_level, heading_text)`。
    第一版只做保守识别，宁可少切，也避免把普通正文误判成标题。
    """

    headings: list[tuple[int, int, str]] = []

    for index, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if not stripped:
            continue
        if not is_plain_text_heading_candidate(stripped):
            continue
        if not is_isolated_plain_text_heading_line(lines, index):
            continue

        headings.append(
            (
                index,
                detect_plain_text_heading_level(stripped),
                stripped,
            )
        )

    return headings


def is_plain_text_heading_candidate(line: str) -> bool:
    """判断一行文本是否像章节标题。"""

    if len(line) > 60:
        return False
    if re.search(r"[。！？；.!?]$", line):
        return False

    heading_patterns = (
        r"^第[0-9一二三四五六七八九十百千零]+[章节部分篇卷]\s*\S*$",
        r"^[0-9]{1,2}(?:\.[0-9]{1,2}){0,2}(?:[、.．)]|\s)\s*\S+.*$",
        r"^[一二三四五六七八九十百千零]+[、.．)]\s*\S+.*$",
        r"^[(（][0-9一二三四五六七八九十百千零]+[)）]\s*\S+.*$",
    )

    return any(re.match(pattern, line) for pattern in heading_patterns)


def is_isolated_plain_text_heading_line(lines: list[str], index: int) -> bool:
    """判断标题候选行是否足够独立。"""

    previous_line = lines[index - 1].strip() if index > 0 else ""
    next_line = lines[index + 1].strip() if index + 1 < len(lines) else ""

    return not previous_line or not next_line


def detect_plain_text_heading_level(line: str) -> int:
    """根据标题样式给纯文本标题一个粗粒度层级。"""

    if re.match(r"^第[0-9一二三四五六七八九十百千零]+[章节部分篇卷]\s*\S*$", line):
        return 1
    numeric_match = re.match(r"^([0-9]{1,2}(?:\.[0-9]{1,2}){0,2})(?:[、.．)]|\s)\s*\S+.*$", line)
    if numeric_match:
        return min(numeric_match.group(1).count(".") + 1, 6)
    return 2


def build_plain_text_heading_sections(
    lines: list[str],
    heading_candidates: list[tuple[int, int, str]],
    file_type: str,
) -> list[Section]:
    """按检测到的标题切纯文本 Section。"""

    sections: list[Section] = []
    first_heading_index = heading_candidates[0][0]
    if any(line.strip() for line in lines[:first_heading_index]):
        sections.append(
            Section(
                heading_path=[],
                level=0,
                blocks=build_plain_text_paragraph_blocks(
                    lines[:first_heading_index],
                    file_type=file_type,
                    heading_path=[],
                ),
                metadata={
                    "file_type": file_type,
                    "splitter": "plain_text_structure",
                },
            )
        )

    for candidate_index, (start_index, level, heading_text) in enumerate(heading_candidates):
        end_index = (
            heading_candidates[candidate_index + 1][0]
            if candidate_index + 1 < len(heading_candidates)
            else len(lines)
        )
        section_lines = lines[start_index:end_index]
        heading_path = [heading_text]
        blocks = build_plain_text_heading_blocks(
            section_lines,
            file_type=file_type,
            heading_text=heading_text,
            heading_level=level,
            heading_path=heading_path,
        )
        if not blocks:
            continue

        sections.append(
            Section(
                heading_path=heading_path,
                level=level,
                blocks=blocks,
                metadata={
                    "file_type": file_type,
                    "splitter": "plain_text_heading_structure",
                },
            )
        )

    return sections


def build_plain_text_heading_blocks(
    lines: list[str],
    *,
    file_type: str,
    heading_text: str,
    heading_level: int,
    heading_path: list[str],
) -> list[Block]:
    """为按标题切出来的纯文本 Section 构造 blocks。"""

    blocks = [
        Block(
            block_type="heading",
            content=heading_text,
            metadata={
                "file_type": file_type,
                "block_type": "heading",
                "heading_path": heading_path.copy(),
                "heading_level": heading_level,
                "paragraph_start": 0,
                "paragraph_end": 0,
                "splitter": "plain_text_heading_block",
            },
        )
    ]

    paragraph_blocks = build_plain_text_paragraph_blocks(
        lines[1:],
        file_type=file_type,
        heading_path=heading_path,
        starting_paragraph_index=1,
    )
    blocks.extend(paragraph_blocks)
    return blocks


def build_plain_text_paragraph_section(text: str, file_type: str) -> list[Section]:
    """退回原来的纯段落切分模式。"""

    blocks = build_plain_text_paragraph_blocks(
        text.splitlines(),
        file_type=file_type,
        heading_path=[],
    )

    return [
        Section(
            heading_path=[],
            level=0,
            blocks=blocks,
            metadata={
                "file_type": file_type,
                "splitter": "plain_text_structure",
            },
        )
    ]


def build_plain_text_paragraph_blocks(
    lines: list[str],
    *,
    file_type: str,
    heading_path: list[str],
    starting_paragraph_index: int = 0,
) -> list[Block]:
    """把纯文本行按空行切成 paragraph blocks。"""

    text = "\n".join(lines)
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", text) if part.strip()]
    if not paragraphs and text.strip():
        paragraphs = [text.strip()]

    blocks: list[Block] = []
    for index, paragraph in enumerate(paragraphs):
        blocks.append(
            Block(
                block_type="paragraph",
                content=paragraph,
                metadata={
                    "file_type": file_type,
                    "block_type": "paragraph",
                    "heading_path": heading_path.copy(),
                    "paragraph_start": starting_paragraph_index + index,
                    "paragraph_end": starting_paragraph_index + index,
                    "splitter": "plain_text_block",
                },
            )
        )

    return blocks


def split_pdf_sections(pages: list[PdfPageText]) -> list[Section]:
    """按 PDF 页码和页内段落切 Section / Block。"""

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
    current_section_key: int | None = None

    for block in prepared_blocks:
        content = normalize_text(block.content)
        if not content:
            continue

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
    current_data_lines: list[str] = []
    table_blocks: list[Block] = []

    def emit_table_block(rows: list[str]) -> None:
        if not rows and not header_lines:
            return
        content_lines = [*header_lines, *rows]
        table_blocks.append(
            Block(
                block_type="table",
                content="\n".join(content_lines).strip(),
                metadata={
                    **block.metadata,
                    "splitter": f"{block.metadata.get('splitter', 'table')}_rows",
                },
            )
        )

    for row in data_lines:
        candidate_rows = [*current_data_lines, row]
        candidate_text = "\n".join([*header_lines, *candidate_rows]).strip()

        if current_data_lines and len(candidate_text) > max_chunk_size:
            emit_table_block(current_data_lines)
            overlap_rows = build_table_overlap_rows(current_data_lines, header_lines, chunk_overlap)
            current_data_lines = [*overlap_rows, row]
            candidate_text = "\n".join([*header_lines, *current_data_lines]).strip()
            if len(candidate_text) <= max_chunk_size:
                continue

        if len(candidate_text) <= max_chunk_size:
            current_data_lines = candidate_rows
            continue

        if current_data_lines:
            emit_table_block(current_data_lines)
            current_data_lines = []

        available_size = max(
            1,
            max_chunk_size - base_length,
        )
        row_fragments = split_text_to_windows(row, available_size, chunk_overlap)
        for fragment in row_fragments:
            emit_table_block([fragment])

    if current_data_lines:
        emit_table_block(current_data_lines)

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

    content = prepend_markdown_heading_prefix(content, metadata_items)

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
    metadata_items: list[dict[str, Any]],
    target_chunk_size: int,
    max_chunk_size: int,
    chunk_overlap: int,
) -> dict[str, Any]:
    """合并多个 Block 的 metadata。"""

    if not metadata_items:
        return {
            "target_chunk_size": target_chunk_size,
            "max_chunk_size": max_chunk_size,
            "chunk_overlap": chunk_overlap,
            "splitter": "unknown",
        }

    first = metadata_items[0]
    merged: dict[str, Any] = {
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

    for key in ("page", "page_start", "paragraph_start"):
        values = [item.get(key) for item in metadata_items if item.get(key) is not None]
        if values:
            merged[key] = min(values)

    for key in ("page_end", "paragraph_end"):
        values = [item.get(key) for item in metadata_items if item.get(key) is not None]
        if values:
            merged[key] = max(values)

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
        if is_markdown_list_line(stripped):
            if current_item:
                items.append(current_item)
            current_item = [line]
            continue
        if current_item and is_list_continuation_line(line):
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


def build_table_overlap_rows(
    rows: list[str],
    header_lines: list[str],
    chunk_overlap: int,
) -> list[str]:
    """表格 overlap 只复制完整数据行，避免新 chunk 从表格中间起步。"""

    if chunk_overlap <= 0:
        return []

    overlap_rows: list[str] = []
    total = len("\n".join(header_lines)) if header_lines else 0

    for row in reversed(rows):
        row_length = len(row) + 1
        if overlap_rows and total + row_length > chunk_overlap:
            break
        if not overlap_rows and total + row_length > chunk_overlap:
            return []
        overlap_rows.append(row)
        total += row_length

    return list(reversed(overlap_rows))


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


def prepend_markdown_heading_prefix(
    content: str,
    metadata_items: list[dict[str, Any]],
) -> str:
    """确保 Markdown chunk 带标题前缀。"""

    if not metadata_items:
        return content

    first = metadata_items[0]
    if first.get("file_type") != "md":
        return content

    heading_path = first.get("heading_path") or []
    if not heading_path:
        return content

    level = max(1, min(len(heading_path), 6))
    heading_line = f"{'#' * level} {heading_path[-1]}"

    if content.startswith(heading_line):
        return content

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


def normalize_text(text: str) -> str:
    """标准化换行和首尾空白，避免空 chunk。"""

    return text.replace("\r\n", "\n").replace("\r", "\n").strip()
