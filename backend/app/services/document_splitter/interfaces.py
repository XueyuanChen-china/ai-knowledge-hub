from typing import Any, Protocol

from app.services.document_splitter.models import Block, ChunkData, DocumentElement, Section, SplitterOptions


class DocumentParser(Protocol):
    """原始输入 -> DocumentElement[]。"""

    def parse(self, source: Any) -> list[DocumentElement]:
        ...


class DocumentNormalizer(Protocol):
    """DocumentElement[] -> 清洗后的 DocumentElement[]。"""

    def normalize(self, elements: list[DocumentElement]) -> list[DocumentElement]:
        ...


class SectionBuilder(Protocol):
    """DocumentElement[] -> Section[]。"""

    def build_sections(self, elements: list[DocumentElement]) -> list[Section]:
        ...


class ChunkAssembler(Protocol):
    """Block[] -> ChunkData[]。"""

    def assemble(self, blocks: list[Block], options: SplitterOptions) -> list[ChunkData]:
        ...
