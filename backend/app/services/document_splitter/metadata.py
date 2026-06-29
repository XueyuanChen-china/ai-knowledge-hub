from typing import Any, Optional


MetadataDict = dict[str, Any]


def merge_metadata_dicts(*metadata_items: Optional[MetadataDict]) -> MetadataDict:
    """合并多个 metadata 字典。

    后传入的字段覆盖前面的字段，方便 parser / normalizer / builder 分层追加元信息。
    """

    merged: MetadataDict = {}
    for item in metadata_items:
        if item:
            merged.update(item)
    return merged
