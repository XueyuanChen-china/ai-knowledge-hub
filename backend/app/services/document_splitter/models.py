from dataclasses import dataclass, field
from typing import Any, Optional


DEFAULT_TARGET_CHUNK_SIZE = 850
DEFAULT_MAX_CHUNK_SIZE = 1000
DEFAULT_CHUNK_OVERLAP = 200


@dataclass
class SplitterOptions:
    """统一切分参数。"""

    target_chunk_size: int = DEFAULT_TARGET_CHUNK_SIZE
    max_chunk_size: int = DEFAULT_MAX_CHUNK_SIZE
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP


@dataclass
class DocumentElement:
    """统一中间层元素。

    所有 parser 的第一目标，都是把原始文件转换成 DocumentElement 列表。
    """

    element_id: Optional[str] = None
    parent_id: Optional[str] = None
    source_index: Optional[int] = None

    element_type: str = "paragraph"
    text: str = ""
    level: Optional[int] = None

    page_start: Optional[int] = None
    page_end: Optional[int] = None

    sheet_name: Optional[str] = None
    row_start: Optional[int] = None
    row_end: Optional[int] = None
    col_start: Optional[str] = None
    col_end: Optional[str] = None

    bbox: Optional[list[float]] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Block:
    """Section 内的结构块。"""

    block_type: str
    content: str
    metadata: dict[str, Any]


@dataclass
class Section:
    """文档里的语义分区。"""

    heading_path: list[str]
    level: int
    blocks: list[Block]
    metadata: dict[str, Any]


@dataclass
class ChunkData:
    """最终准备写入 chunks 表的数据。"""

    content: str
    metadata: dict[str, Any]


@dataclass
class PdfPageText:
    """PDF 单页文本。"""

    page_number: int
    text: str
