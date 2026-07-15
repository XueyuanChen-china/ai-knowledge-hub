import re
from dataclasses import dataclass
from typing import Optional

from app.services.document_splitter.models import DocumentElement, PdfPageText
from app.services.document_splitter.normalizer import normalize_document_text


HEADING_CONFIDENCE_THRESHOLD = 0.85


@dataclass
class PlainTextLineRecord:
    """纯文本解析时的行级记录。"""

    text: str
    line_index: int
    page_number: Optional[int] = None


@dataclass
class PlainTextHeadingCandidate:
    """纯文本标题候选。"""

    line_index: int
    level: int
    text: str
    confidence: float
    pattern: str
    page_number: Optional[int] = None


def parse_plain_text_elements(text: str, file_type: str = "txt") -> list[DocumentElement]:
    """把纯文本解析成 DocumentElement 列表。"""

    normalized_text = normalize_document_text(text)
    records = build_line_records_from_text(normalized_text)
    return build_plain_text_elements_from_records(records, file_type)


def parse_plain_text_elements_from_pages(
    pages: list[PdfPageText],
    file_type: str = "pdf",
) -> Optional[list[DocumentElement]]:
    """把 PDF 页文本当作 plain text fallback 解析。

    只要检测到可靠标题，就返回 heading-based elements。
    如果完全检测不到标题，再返回 None，让上层继续走按页 fallback。
    """

    records = build_line_records_from_pages(pages)
    heading_candidates = detect_plain_text_heading_candidates(records)
    if not heading_candidates:
        return None

    return build_heading_based_plain_text_elements_from_records(
        records,
        heading_candidates,
        file_type,
    )


def detect_plain_text_headings(lines: list[str]) -> list[tuple[int, int, str]]:
    """检测纯文本中的可靠标题。"""

    records = build_line_records_from_lines(lines)
    return [
        (candidate.line_index, candidate.level, candidate.text)
        for candidate in detect_plain_text_heading_candidates(records)
    ]


def detect_plain_text_heading_candidates(
    records: list[PlainTextLineRecord],
) -> list[PlainTextHeadingCandidate]:
    """检测带置信度的纯文本标题候选。"""

    candidates: list[PlainTextHeadingCandidate] = []

    for index, record in enumerate(records):
        normalized_line = normalize_possible_heading_text(record.text)
        if not normalized_line:
            continue

        match_result = match_heading_pattern(normalized_line)
        if match_result is None:
            continue

        level, pattern_name, base_confidence = match_result
        confidence = score_plain_text_heading_candidate(
            records,
            index,
            normalized_line,
            base_confidence,
        )
        if confidence < HEADING_CONFIDENCE_THRESHOLD:
            continue

        candidates.append(
            PlainTextHeadingCandidate(
                line_index=record.line_index,
                level=level,
                text=normalized_line,
                confidence=confidence,
                pattern=pattern_name,
                page_number=record.page_number,
            )
        )

    return filter_heading_candidates_with_body(records, candidates)


def is_plain_text_heading_candidate(line: str) -> bool:
    """判断一行文本是否像章节标题。"""

    normalized_line = normalize_possible_heading_text(line)
    if not normalized_line:
        return False

    return match_heading_pattern(normalized_line) is not None


def is_isolated_plain_text_heading_line(lines: list[str], index: int) -> bool:
    """判断标题候选行是否足够独立。"""

    records = build_line_records_from_lines(lines)
    if index < 0 or index >= len(records):
        return False

    normalized_line = normalize_possible_heading_text(records[index].text)
    if not normalized_line or not is_plain_text_heading_candidate(normalized_line):
        return False

    previous_line = get_previous_nonempty_line(records, index)
    next_line = get_next_nonempty_line(records, index)
    previous_blank = is_previous_line_blank(records, index)
    next_blank = is_next_line_blank(records, index)

    if previous_blank or next_blank:
        return True

    return looks_like_body_line(next_line) and len(normalized_line) <= 30


def detect_plain_text_heading_level(line: str) -> int:
    """根据标题样式给纯文本标题一个粗粒度层级。"""

    normalized_line = normalize_possible_heading_text(line)
    match_result = match_heading_pattern(normalized_line)
    if match_result is None:
        return 2

    level, _pattern_name, _base_confidence = match_result
    return level


def build_heading_based_plain_text_elements(
    lines: list[str],
    heading_candidates: list[tuple[int, int, str]],
    file_type: str,
) -> list[DocumentElement]:
    """兼容旧接口：按标题构建纯文本 elements。"""

    records = build_line_records_from_lines(lines)
    candidates = [
        PlainTextHeadingCandidate(
            line_index=line_index,
            level=level,
            text=heading_text,
            confidence=1.0,
            pattern="legacy_tuple",
        )
        for line_index, level, heading_text in heading_candidates
    ]
    return build_heading_based_plain_text_elements_from_records(
        records,
        candidates,
        file_type,
    )


def build_paragraph_only_plain_text_elements(
    lines: list[str],
    file_type: str,
) -> list[DocumentElement]:
    """兼容旧接口：只按段落构建纯文本 elements。"""

    records = build_line_records_from_lines(lines)
    return build_paragraph_only_plain_text_elements_from_records(records, file_type)


def build_plain_text_paragraph_elements(
    lines: list[str],
    *,
    file_type: str,
    heading_path: list[str],
    start_source_index: int,
) -> list[DocumentElement]:
    """兼容旧接口：按段落构建纯文本 paragraph elements。"""

    records = build_line_records_from_lines(lines)
    return build_plain_text_paragraph_elements_from_records(
        records,
        file_type=file_type,
        heading_path=heading_path,
        start_source_index=start_source_index,
    )


def build_plain_text_elements_from_records(
    records: list[PlainTextLineRecord],
    file_type: str,
) -> list[DocumentElement]:
    """从行记录构建纯文本 elements。"""

    heading_candidates = detect_plain_text_heading_candidates(records)
    if len(heading_candidates) >= 2:
        return build_heading_based_plain_text_elements_from_records(
            records,
            heading_candidates,
            file_type,
        )

    return build_paragraph_only_plain_text_elements_from_records(records, file_type)


def build_heading_based_plain_text_elements_from_records(
    records: list[PlainTextLineRecord],
    heading_candidates: list[PlainTextHeadingCandidate],
    file_type: str,
) -> list[DocumentElement]:
    """按标题候选构建带层级的纯文本 elements。"""

    elements: list[DocumentElement] = []
    source_index = 0
    current_heading_stack: list[str] = []

    first_heading_index = heading_candidates[0].line_index
    if has_substantive_content(records[:first_heading_index]):
        preface_elements = build_plain_text_paragraph_elements_from_records(
            records[:first_heading_index],
            file_type=file_type,
            heading_path=[],
            start_source_index=source_index,
        )
        elements.extend(preface_elements)
        source_index += len(preface_elements)

    for candidate_index, heading_candidate in enumerate(heading_candidates):
        end_index = (
            heading_candidates[candidate_index + 1].line_index
            if candidate_index + 1 < len(heading_candidates)
            else len(records)
        )
        effective_level = normalize_plain_text_heading_level(
            heading_candidate.level,
            current_heading_stack,
            heading_candidate.pattern,
        )
        current_heading_stack = current_heading_stack[: max(effective_level - 1, 0)]
        current_heading_stack.append(heading_candidate.text)
        heading_path = current_heading_stack.copy()

        heading_metadata = {
            "file_type": file_type,
            "heading_path": heading_path.copy(),
            "heading_level": heading_candidate.level,
            "heading_confidence": heading_candidate.confidence,
            "heading_pattern": heading_candidate.pattern,
            "block_type": "heading",
            "splitter": "plain_text_heading_block",
            "source_parser": "plain_text_parser",
        }
        if heading_candidate.page_number is not None:
            heading_metadata["page_start"] = heading_candidate.page_number
            heading_metadata["page_end"] = heading_candidate.page_number

        elements.append(
            DocumentElement(
                source_index=source_index,
                element_type="heading",
                text=heading_candidate.text,
                level=heading_candidate.level,
                page_start=heading_candidate.page_number,
                page_end=heading_candidate.page_number,
                metadata=heading_metadata,
            )
        )
        source_index += 1

        paragraph_elements = build_plain_text_paragraph_elements_from_records(
            records[heading_candidate.line_index + 1:end_index],
            file_type=file_type,
            heading_path=heading_path,
            start_source_index=source_index,
        )
        elements.extend(paragraph_elements)
        source_index += len(paragraph_elements)

    return elements


def normalize_plain_text_heading_level(
    detected_level: int,
    current_heading_stack: list[str],
    current_pattern: str,
) -> int:
    """把纯文本标题层级做一层轻量归一化。

    目的：
    - 如果当前还没有父级标题，`一、二、三` 这种枚举标题不应该直接从 level 2 开始，
      否则后续会被错误地串成父子关系。
    - 如果层级跨度过大，也做一下收敛，避免直接从空栈跳到更深层。
    """

    if detected_level <= 1:
        return 1

    if not current_heading_stack:
        return 1

    if current_pattern in {"cn_enum", "paren_enum"}:
        current_heading_match = match_heading_pattern(current_heading_stack[-1])
        current_heading_pattern = (
            current_heading_match[1] if current_heading_match is not None else None
        )
        if current_heading_pattern == current_pattern:
            return len(current_heading_stack)

    return min(detected_level, len(current_heading_stack) + 1)


def build_paragraph_only_plain_text_elements_from_records(
    records: list[PlainTextLineRecord],
    file_type: str,
) -> list[DocumentElement]:
    """只按段落构建纯文本 elements。"""

    return build_plain_text_paragraph_elements_from_records(
        records,
        file_type=file_type,
        heading_path=[],
        start_source_index=0,
    )


def build_plain_text_paragraph_elements_from_records(
    records: list[PlainTextLineRecord],
    *,
    file_type: str,
    heading_path: list[str],
    start_source_index: int,
) -> list[DocumentElement]:
    """按段落边界把行记录聚合成 paragraph elements。"""

    elements: list[DocumentElement] = []
    current_lines: list[str] = []
    current_pages: list[int] = []
    paragraph_index = 0

    def flush_current_paragraph() -> None:
        nonlocal paragraph_index

        content = "\n".join(current_lines).strip()
        if not content:
            current_lines.clear()
            current_pages.clear()
            return

        metadata = {
            "file_type": file_type,
            "heading_path": heading_path.copy(),
            "block_type": "paragraph",
            "splitter": "plain_text_block",
            "source_parser": "plain_text_parser",
        }
        if current_pages:
            metadata["page_start"] = current_pages[0]
            metadata["page_end"] = current_pages[-1]

        elements.append(
            DocumentElement(
                source_index=start_source_index + paragraph_index,
                element_type="paragraph",
                text=content,
                page_start=current_pages[0] if current_pages else None,
                page_end=current_pages[-1] if current_pages else None,
                metadata=metadata,
            )
        )
        paragraph_index += 1
        current_lines.clear()
        current_pages.clear()

    for record in records:
        stripped = record.text.strip()
        if not stripped:
            flush_current_paragraph()
            continue

        current_lines.append(stripped)
        if record.page_number is not None:
            if not current_pages or current_pages[-1] != record.page_number:
                current_pages.append(record.page_number)

    flush_current_paragraph()
    return elements


def build_line_records_from_text(text: str) -> list[PlainTextLineRecord]:
    return build_line_records_from_lines(text.splitlines())


def build_line_records_from_lines(lines: list[str]) -> list[PlainTextLineRecord]:
    return [
        PlainTextLineRecord(text=line, line_index=index)
        for index, line in enumerate(lines)
    ]


def build_line_records_from_pages(pages: list[PdfPageText]) -> list[PlainTextLineRecord]:
    """把 PDF 页面打平成统一行记录。"""

    records: list[PlainTextLineRecord] = []
    line_index = 0

    for page_index, page in enumerate(pages):
        normalized_page_text = normalize_document_text(page.text)
        page_lines = normalized_page_text.splitlines()
        if not page_lines:
            page_lines = [""]

        for line in page_lines:
            records.append(
                PlainTextLineRecord(
                    text=line,
                    line_index=line_index,
                    page_number=page.page_number,
                )
            )
            line_index += 1

        if page_index + 1 < len(pages) and records and records[-1].text.strip():
            records.append(
                PlainTextLineRecord(
                    text="",
                    line_index=line_index,
                    page_number=page.page_number,
                )
            )
            line_index += 1

    return records


def normalize_possible_heading_text(line: str) -> str:
    """对可能的标题行做轻量清洗，增强 OCR / PDF 文本容错。"""

    normalized_line = re.sub(r"\s+", " ", line.strip())
    normalized_line = re.sub(r"第\s+([0-9一二三四五六七八九十百千零]+)\s*([章节部分篇卷])", r"第\1\2", normalized_line)
    normalized_line = re.sub(r"(?<=[0-9])\s*[.．]\s*(?=[0-9])", ".", normalized_line)
    normalized_line = compact_spaced_cjk_heading_tokens(normalized_line)
    return normalized_line


def match_heading_pattern(line: str) -> Optional[tuple[int, str, float]]:
    """匹配标题模式，并返回 level / pattern / base_confidence。"""

    if not line or len(line) > 60:
        return None
    if re.search(r"[。！？；.!?]$", line):
        return None
    if looks_like_noise_line(line):
        return None
    if re.search(r"\.{4,}|·{3,}|…{2,}", line):
        return None

    if re.match(r"^第[0-9一二三四五六七八九十百千零]+[章节部分篇卷](?:\s*\S+.*)?$", line):
        return (1, "chapter_cn", 0.97)

    numeric_match = re.match(r"^([0-9]{1,2}(?:\.[0-9]{1,2}){0,3})(?:[、.．)]|\s)\s*\S+.*$", line)
    if numeric_match:
        level = min(numeric_match.group(1).count(".") + 1, 6)
        return (level, "numeric", 0.94)

    if re.match(r"^[一二三四五六七八九十百千零]+[、.．)]\s*\S+.*$", line):
        return (2, "cn_enum", 0.87)

    if re.match(r"^[(（][0-9一二三四五六七八九十百千零]+[)）]\s*\S+.*$", line):
        return (2, "paren_enum", 0.85)

    return None


def score_plain_text_heading_candidate(
    records: list[PlainTextLineRecord],
    index: int,
    line: str,
    base_confidence: float,
) -> float:
    """根据上下文给标题候选打分。"""

    confidence = base_confidence
    previous_line = get_previous_nonempty_line(records, index)
    next_line = get_next_nonempty_line(records, index)

    if is_previous_line_blank(records, index):
        confidence += 0.03
    if is_next_line_blank(records, index):
        confidence += 0.03
    if looks_like_body_line(next_line):
        confidence += 0.04
    if previous_line and re.search(r"[。！？；.!?]$", previous_line):
        confidence += 0.02

    if len(line) <= 20:
        confidence += 0.02
    elif len(line) > 40:
        confidence -= 0.06

    if line.count(" ") >= 5:
        confidence -= 0.04

    return min(max(confidence, 0.0), 1.0)


def filter_heading_candidates_with_body(
    records: list[PlainTextLineRecord],
    candidates: list[PlainTextHeadingCandidate],
) -> list[PlainTextHeadingCandidate]:
    """过滤掉目录、连续大纲等没有正文承接的标题候选。"""

    filtered: list[PlainTextHeadingCandidate] = []

    for candidate_index, candidate in enumerate(candidates):
        end_index = (
            candidates[candidate_index + 1].line_index
            if candidate_index + 1 < len(candidates)
            else len(records)
        )
        body_records = records[candidate.line_index + 1:end_index]
        if has_substantive_content(body_records):
            filtered.append(candidate)

    return filtered


def has_substantive_content(records: list[PlainTextLineRecord]) -> bool:
    """判断一段记录中是否有像正文的内容。"""

    for record in records:
        stripped = record.text.strip()
        if not stripped:
            continue
        if looks_like_noise_line(stripped):
            continue
        return True

    return False


def looks_like_noise_line(line: str) -> bool:
    """过滤页码、孤立编号等明显噪声。"""

    compact_line = line.strip()
    lowered_line = compact_line.lower()

    noise_patterns = (
        r"^第?\s*[0-9]+\s*页$",
        r"^page\s*[0-9]+$",
        r"^[0-9]+$",
        r"^[ivxlcdm]+$",
        r"^[-_=]{3,}$",
    )
    return any(re.match(pattern, lowered_line) for pattern in noise_patterns)


def looks_like_body_line(line: str) -> bool:
    """粗略判断一行更像正文，而不是标题。"""

    stripped = normalize_possible_heading_text(line)
    if not stripped:
        return False
    if re.search(r"[。！？；.!?]$", stripped):
        return True
    return len(stripped) >= 12


def compact_spaced_cjk_heading_tokens(line: str) -> str:
    """只在明显 OCR 拆字时，才合并中文 token 间的空格。"""

    tokens = [token for token in line.split(" ") if token]
    if len(tokens) < 3:
        return line

    if all(re.fullmatch(r"[\u4e00-\u9fff]", token) for token in tokens):
        return "".join(tokens)

    first_token = tokens[0]
    remaining_tokens = tokens[1:]
    if remaining_tokens and all(re.fullmatch(r"[\u4e00-\u9fff]", token) for token in remaining_tokens):
        if re.fullmatch(r"第[0-9一二三四五六七八九十百千零]+[章节部分篇卷]", first_token):
            return first_token + " " + "".join(remaining_tokens)
        if re.fullmatch(r"[0-9]{1,2}(?:\.[0-9]{1,2}){0,3}", first_token):
            return first_token + " " + "".join(remaining_tokens)

    return line


def get_previous_nonempty_line(records: list[PlainTextLineRecord], index: int) -> str:
    for candidate_index in range(index - 1, -1, -1):
        stripped = records[candidate_index].text.strip()
        if stripped:
            return stripped
    return ""


def get_next_nonempty_line(records: list[PlainTextLineRecord], index: int) -> str:
    for candidate_index in range(index + 1, len(records)):
        stripped = records[candidate_index].text.strip()
        if stripped:
            return stripped
    return ""


def is_previous_line_blank(records: list[PlainTextLineRecord], index: int) -> bool:
    if index == 0:
        return True
    return not records[index - 1].text.strip()


def is_next_line_blank(records: list[PlainTextLineRecord], index: int) -> bool:
    if index + 1 >= len(records):
        return True
    return not records[index + 1].text.strip()
