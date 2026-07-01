from app.services.document_splitter.models import DocumentElement


def normalize_file_type(file_type: str) -> str:
    """统一文件类型大小写。"""

    return file_type.lower().strip()


def normalize_document_text(text: str) -> str:
    """标准化输入文本的换行。"""

    return text.replace("\r\n", "\n").replace("\r", "\n")


def normalize_text(text: str) -> str:
    """标准化换行和首尾空白，避免空 chunk。"""

    return normalize_document_text(text).strip()


class IdentityDocumentNormalizer:
    """Phase 1 的最小 normalizer。

    当前先提供统一接口形状，后续再逐步补充页眉页脚去重、OCR 清洗等能力。
    """

    def normalize(self, elements: list[DocumentElement]) -> list[DocumentElement]:
        return elements
