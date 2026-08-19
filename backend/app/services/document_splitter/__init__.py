"""多格式文档切分基础设施。"""

from app.services.document_splitter.interfaces import (
    ChunkAssembler,
    DocumentNormalizer,
    DocumentParser,
    SectionBuilder,
)
from app.services.document_splitter.metadata import MetadataDict, merge_metadata_dicts
from app.services.document_splitter.models import (
    Block,
    ChunkData,
    DocumentElement,
    PdfPageText,
    Section,
    SplitterOptions,
)
from app.services.document_splitter.evaluation import (
    build_splitter_regression_snapshot,
    evaluate_splitter_regression_snapshot,
)
from app.services.document_splitter.splitter import split_document_text
from app.services.document_splitter.splitter import build_document_elements

__all__ = [
    "Block",
    "build_document_elements",
    "build_splitter_regression_snapshot",
    "ChunkAssembler",
    "ChunkData",
    "DocumentElement",
    "DocumentNormalizer",
    "DocumentParser",
    "evaluate_splitter_regression_snapshot",
    "MetadataDict",
    "PdfPageText",
    "Section",
    "SectionBuilder",
    "split_document_text",
    "SplitterOptions",
    "merge_metadata_dicts",
]
