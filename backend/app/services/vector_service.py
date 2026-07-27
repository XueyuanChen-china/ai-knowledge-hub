import json
from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from json import JSONDecodeError
from typing import Any, Iterable, Optional

from app.config import get_settings
from app.db.models import Chunk


@dataclass
class VectorIndexResult:
    """一次向量入库的结果。"""

    index_name: str
    vector_ids: list[str]


@dataclass
class SemanticSearchHit:
    """一次语义检索返回的单条结果。"""

    vector_id: str
    chunk_id: Optional[int]
    document_id: Optional[int]
    knowledge_item_id: Optional[int]
    content: str
    score: float
    metadata: dict[str, Any]


def build_stable_vector_id(chunk: Chunk) -> str:
    """为 chunk 生成稳定 vector_id。"""

    identity_payload = {
        "knowledge_base_id": chunk.knowledge_base_id,
        "document_id": chunk.document_id,
        "knowledge_item_id": chunk.knowledge_item_id,
        "chunk_index": chunk.chunk_index,
        "content_sha256": sha256(chunk.content.encode("utf-8")).hexdigest(),
    }
    digest = sha256(
        json.dumps(identity_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return f"chunk_{digest}"


def add_chunks(chunks: list[Chunk]) -> VectorIndexResult:
    """把 chunks 写入 Elasticsearch，并返回 vector_id 列表。"""

    if not chunks:
        raise ValueError("chunks must not be empty")

    embeddings = embed_chunks(chunks)
    return index_chunks(chunks, embeddings)


def embed_chunks(chunks: list[Chunk]) -> list[list[float]]:
    """只执行 embedding，不写 Elasticsearch。"""

    if not chunks:
        raise ValueError("chunks must not be empty")

    embedding_model = get_embedding_model()
    texts = [chunk.content for chunk in chunks]
    embeddings = encode_texts(embedding_model, texts)
    validate_embedding_dimensions(embeddings)
    return embeddings


def index_chunks(
    chunks: list[Chunk],
    embeddings: list[list[float]],
    *,
    activate_alias: bool = True,
) -> VectorIndexResult:
    """使用已生成的 embedding 写入 Elasticsearch。"""

    if not chunks:
        raise ValueError("chunks must not be empty")
    if len(chunks) != len(embeddings):
        raise ValueError("chunks and embeddings must have the same length")

    knowledge_base_id = chunks[0].knowledge_base_id
    organization_id = chunks[0].organization_id
    if any(chunk.knowledge_base_id != knowledge_base_id for chunk in chunks):
        raise ValueError("all chunks must belong to the same knowledge base")
    if any(chunk.organization_id != organization_id for chunk in chunks):
        raise ValueError("all chunks must belong to the same organization")

    index_name = build_concrete_index_name(knowledge_base_id)
    client = get_elasticsearch_client()
    ensure_index(client, index_name)
    validate_embedding_dimensions(embeddings)
    vector_ids = [build_stable_vector_id(chunk) for chunk in chunks]
    refresh = get_write_refresh_option()

    actions = (
        {
            "_op_type": "index",
            "_index": index_name,
            "_id": vector_id,
            **build_index_document(chunk, vector_id, embedding),
        }
        for chunk, vector_id, embedding in zip(chunks, vector_ids, embeddings)
    )
    execute_bulk_actions(client, actions, refresh=refresh)
    if activate_alias:
        activate_index_alias(
            client,
            alias_name=build_index_alias_name(knowledge_base_id),
            concrete_index_name=index_name,
        )

    return VectorIndexResult(
        index_name=index_name,
        vector_ids=vector_ids,
    )


def delete_vectors(
    knowledge_base_id: int,
    vector_ids: list[str],
) -> None:
    """从 Elasticsearch 删除一批已存在的向量文档。"""

    if not vector_ids:
        return

    index_name = build_index_name(knowledge_base_id)
    client = get_elasticsearch_client()
    refresh = get_write_refresh_option()
    for vector_id in vector_ids:
        try:
            client.delete(index=index_name, id=vector_id, refresh=refresh)
        except Exception as exc:
            if is_not_found_exception(exc):
                continue
            raise


def search_similar_chunks(
    organization_id: int,
    knowledge_base_id: int,
    query: str,
    *,
    top_k: int = 5,
    num_candidates: Optional[int] = None,
) -> list[SemanticSearchHit]:
    """按问题文本检索相似 chunks。"""

    query = query.strip()
    if not query:
        raise ValueError("query must not be empty")

    index_name = build_index_name(knowledge_base_id)
    client = get_elasticsearch_client()
    if not client.indices.exists(index=index_name):
        return []

    query_vector = encode_query_text(query)
    if num_candidates is None:
        num_candidates = max(top_k * 4, 20)

    response = client.search(
        index=index_name,
        size=top_k,
        knn={
            "field": "embedding",
            "query_vector": query_vector,
            "k": top_k,
            "num_candidates": num_candidates,
            "filter": [
                {"term": {"organization_id": organization_id}},
                {"term": {"knowledge_base_id": knowledge_base_id}},
            ],
        },
    )

    hits: list[SemanticSearchHit] = []
    for raw_hit in response.get("hits", {}).get("hits", []):
        source = raw_hit.get("_source", {})
        metadata = source.get("metadata") or {}
        chunk_id = source.get("chunk_id")
        if chunk_id is None:
            chunk_id = metadata.get("chunk_id")

        hits.append(
            SemanticSearchHit(
                vector_id=source.get("vector_id", raw_hit.get("_id", "")),
                chunk_id=chunk_id,
                document_id=source.get("document_id"),
                knowledge_item_id=source.get("knowledge_item_id"),
                content=source.get("content", ""),
                score=float(raw_hit.get("_score", 0.0)),
                metadata=metadata,
            )
        )

    return hits


def build_index_name(knowledge_base_id: int) -> str:
    """返回业务查询使用的稳定 alias 名称。"""

    return build_index_alias_name(knowledge_base_id)


def build_index_alias_name(knowledge_base_id: int) -> str:
    settings = get_settings()
    return f"{settings.elasticsearch_index_prefix}{knowledge_base_id}_active"


def build_concrete_index_name(knowledge_base_id: int) -> str:
    """返回实际承载当前 mapping 的版本化 ES 索引名。"""

    settings = get_settings()
    return (
        f"{settings.elasticsearch_index_prefix}v"
        f"{settings.elasticsearch_index_version}_{knowledge_base_id}"
    )


def build_index_document(
    chunk: Chunk,
    vector_id: str,
    embedding: list[float],
) -> dict[str, Any]:
    """构造写入 Elasticsearch 的文档。"""

    metadata = build_vector_metadata(chunk)
    searchable_fields = build_searchable_metadata_fields(metadata)

    return {
        "vector_id": vector_id,
        "chunk_id": chunk.id,
        "content": chunk.content,
        "embedding": embedding,
        "organization_id": chunk.organization_id,
        "knowledge_base_id": chunk.knowledge_base_id,
        "document_id": chunk.document_id,
        "knowledge_item_id": chunk.knowledge_item_id,
        "chunk_index": chunk.chunk_index,
        "metadata": metadata,
        **searchable_fields,
        "created_at": chunk.created_at.isoformat() if chunk.created_at is not None else None,
    }


def build_vector_metadata(chunk: Chunk) -> dict[str, Any]:
    """把 chunk.metadata_json 转成适合 Elasticsearch 的 metadata。"""

    metadata = parse_chunk_metadata(chunk.metadata_json)
    metadata.update(
        {
            "chunk_id": chunk.id,
            "organization_id": chunk.organization_id,
            "knowledge_base_id": chunk.knowledge_base_id,
            "document_id": chunk.document_id,
            "knowledge_item_id": chunk.knowledge_item_id,
            "chunk_index": chunk.chunk_index,
        }
    )
    return sanitize_metadata_for_elasticsearch(metadata)


def parse_chunk_metadata(metadata_json: str) -> dict[str, Any]:
    if not metadata_json.strip():
        return {}
    try:
        return json.loads(metadata_json)
    except JSONDecodeError:
        return {}


def sanitize_metadata_for_elasticsearch(metadata: dict[str, Any]) -> dict[str, Any]:
    """把 metadata 清洗成可稳定写入 ES 的结构。"""

    sanitized: dict[str, Any] = {}
    for key, value in metadata.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            sanitized[key] = value
            continue
        if isinstance(value, list):
            sanitized[key] = [
                str(item) if not isinstance(item, (str, int, float, bool)) else item
                for item in value
            ]
            continue
        if isinstance(value, dict):
            sanitized[key] = json.dumps(value, ensure_ascii=False, sort_keys=True)
            continue
        sanitized[key] = str(value)
    return sanitized


def build_searchable_metadata_fields(metadata: dict[str, Any]) -> dict[str, Any]:
    """把常用过滤字段下沉到顶层，方便后续检索和权限过滤。"""

    page_number = metadata.get("page_number")
    page_start = coerce_int(metadata.get("page_start"), fallback=coerce_int(page_number))
    page_end = coerce_int(metadata.get("page_end"), fallback=coerce_int(page_number))

    return {
        "file_type": coerce_keyword_value(metadata.get("file_type")),
        "source_file": coerce_keyword_value(
            metadata.get("source_file") or metadata.get("filename")
        ),
        "page_start": page_start,
        "page_end": page_end,
        "heading_path": coerce_keyword_values(metadata.get("heading_path")),
        "permission_group": coerce_keyword_values(metadata.get("permission_group")),
    }


def coerce_keyword_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, list):
        if not value:
            return None
        return str(value[0])
    return str(value)


def coerce_keyword_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    normalized = str(value).strip()
    return [normalized] if normalized else []


def coerce_int(value: Any, fallback: Optional[int] = None) -> Optional[int]:
    if value is None:
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def validate_embedding_dimensions(embeddings: list[list[float]]) -> None:
    """在写 ES 之前先校验向量维度，避免把错误推迟到 ES 返回。"""

    settings = get_settings()
    expected_dimensions = settings.embedding_dimensions
    for index, embedding in enumerate(embeddings):
        actual_dimensions = len(embedding)
        if actual_dimensions != expected_dimensions:
            raise ValueError(
                f"Embedding dimension mismatch at position {index}: "
                f"expected {expected_dimensions}, got {actual_dimensions}"
            )


def encode_texts(embedding_model, texts: list[str]) -> list[list[float]]:
    """用 BGE-M3 dense embedding 编码文本。"""

    settings = get_settings()
    vectors = embedding_model.encode(
        texts,
        batch_size=settings.embedding_batch_size,
        normalize_embeddings=settings.embedding_normalize,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    if hasattr(vectors, "tolist"):
        return vectors.tolist()
    return [list(vector) for vector in vectors]


def encode_query_text(query: str) -> list[float]:
    """把用户问题编码成单条查询向量。"""

    embedding_model = get_embedding_model()
    vectors = encode_texts(embedding_model, [query])
    validate_embedding_dimensions(vectors)
    return vectors[0]


def get_write_refresh_option():
    """把配置里的 refresh 选项转换成 ES 可接受的值。"""

    raw_value = get_settings().elasticsearch_write_refresh.strip().lower()
    if raw_value == "false":
        return False
    if raw_value == "true":
        return True
    if raw_value == "wait_for":
        return "wait_for"
    raise ValueError(
        "Invalid elasticsearch_write_refresh. Allowed values: false, true, wait_for"
    )


def execute_bulk_actions(
    client,
    actions: Iterable[dict[str, Any]],
    *,
    refresh,
) -> None:
    """统一封装 Elasticsearch bulk 写入。"""

    try:
        from elasticsearch.helpers import bulk
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "elasticsearch helpers are required for bulk indexing. Install it with `pip install elasticsearch`."
        ) from exc

    bulk(
        client,
        actions,
        refresh=refresh,
    )


@lru_cache
def get_embedding_model():
    """懒加载 BGE-M3 embedding 模型。"""

    try:
        from sentence_transformers import SentenceTransformer
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "sentence-transformers is required for embeddings. Install it with `pip install sentence-transformers transformers`."
        ) from exc

    settings = get_settings()
    return SentenceTransformer(
        settings.embedding_model_name,
        device=settings.embedding_device,
    )


@lru_cache
def get_elasticsearch_client():
    """懒加载 Elasticsearch 客户端。"""

    try:
        from elasticsearch import Elasticsearch
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "elasticsearch is required for vector storage. Install it with `pip install elasticsearch`."
        ) from exc

    settings = get_settings()
    basic_auth = None
    if settings.elasticsearch_username and settings.elasticsearch_password:
        basic_auth = (settings.elasticsearch_username, settings.elasticsearch_password)

    return Elasticsearch(
        settings.elasticsearch_url,
        basic_auth=basic_auth,
        verify_certs=settings.elasticsearch_verify_certs,
    )


def ensure_index(client, index_name: str) -> None:
    """确保 Elasticsearch 索引存在，并带有 dense_vector mapping。"""

    if client.indices.exists(index=index_name):
        return

    settings = get_settings()
    client.indices.create(
        index=index_name,
        mappings={
            "properties": {
                "vector_id": {"type": "keyword"},
                "chunk_id": {"type": "integer"},
                "organization_id": {"type": "integer"},
                "content": {
                    "type": "text",
                    "analyzer": settings.elasticsearch_content_analyzer,
                    "search_analyzer": settings.elasticsearch_content_search_analyzer,
                },
                "embedding": {
                    "type": "dense_vector",
                    "dims": settings.embedding_dimensions,
                    "index": True,
                    "similarity": "cosine",
                },
                "knowledge_base_id": {"type": "integer"},
                "document_id": {"type": "integer"},
                "knowledge_item_id": {"type": "integer"},
                "chunk_index": {"type": "integer"},
                "file_type": {"type": "keyword"},
                "source_file": {"type": "keyword"},
                "page_start": {"type": "integer"},
                "page_end": {"type": "integer"},
                "heading_path": {"type": "keyword"},
                "permission_group": {"type": "keyword"},
                "metadata": {"type": "object", "enabled": False},
                "created_at": {"type": "date"},
            }
        },
    )


def activate_index_alias(client, *, alias_name: str, concrete_index_name: str) -> None:
    """把查询 alias 原子地切到指定具体索引。

    旧索引不会被删除。重建完成前客户端仍走旧 alias，重建完成后一次请求切换，
    因而 PostgreSQL 与 ES 的授权字段不会长期处于半同步状态。
    """

    try:
        current_indices = list(client.indices.get_alias(name=alias_name).keys())
    except Exception as exc:
        if not is_not_found_exception(exc):
            raise
        current_indices = []
    actions = [
        {"remove": {"index": index_name, "alias": alias_name}}
        for index_name in current_indices
        if index_name != concrete_index_name
    ]
    if concrete_index_name not in current_indices:
        actions.append({"add": {"index": concrete_index_name, "alias": alias_name}})
    if actions:
        client.indices.update_aliases(actions=actions)


def is_not_found_exception(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code == 404:
        return True

    meta = getattr(exc, "meta", None)
    if meta is not None and getattr(meta, "status", None) == 404:
        return True

    return False
