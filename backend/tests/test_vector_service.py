import json
import sys
import unittest
from types import SimpleNamespace
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db.models import Chunk
from app.services import vector_service


class FakeEmbeddingModel:
    def encode(self, texts, **kwargs):
        return [[float(len(text))] * 1024 for text in texts]


class FakeNotFoundError(Exception):
    def __init__(self):
        self.meta = SimpleNamespace(status=404)


class FakeIndicesClient:
    def __init__(self):
        self.created = []
        self.exists_result = False
        self.aliases = {}

    def exists(self, index):
        return self.exists_result

    def create(self, index, mappings, settings=None):
        self.created.append(
            {
                "index": index,
                "mappings": mappings,
                "settings": settings,
            }
        )

    def get_alias(self, name):
        matching = {
            index: {"aliases": {name: {}}}
            for index, aliases in self.aliases.items()
            if name in aliases
        }
        if not matching:
            raise FakeNotFoundError()
        return matching

    def update_aliases(self, actions):
        for action in actions:
            operation, payload = next(iter(action.items()))
            aliases = self.aliases.setdefault(payload["index"], set())
            if operation == "add":
                aliases.add(payload["alias"])
            elif operation == "remove":
                aliases.discard(payload["alias"])


class FakeElasticsearchClient:
    def __init__(self):
        self.indices = FakeIndicesClient()
        self.last_delete_ids = None
        self.last_search = None
        self.search_response = {"hits": {"hits": []}}

    def delete(self, index, id, refresh=None):
        self.last_delete_ids = self.last_delete_ids or []
        self.last_delete_ids.append({"index": index, "id": id, "refresh": refresh})

    def search(self, index, size, knn=None, query=None):
        self.last_search = {
            "index": index,
            "size": size,
            "knn": knn,
            "query": query,
        }
        return self.search_response


class VectorServiceTests(unittest.TestCase):
    def build_settings(self, **overrides):
        return SimpleNamespace(
            elasticsearch_index_prefix=overrides.get("elasticsearch_index_prefix", "knowledge_chunks_"),
            elasticsearch_index_version=overrides.get("elasticsearch_index_version", 2),
            elasticsearch_content_analyzer=overrides.get("elasticsearch_content_analyzer", "cjk"),
            elasticsearch_content_search_analyzer=overrides.get(
                "elasticsearch_content_search_analyzer",
                "cjk",
            ),
            elasticsearch_write_refresh=overrides.get("elasticsearch_write_refresh", "wait_for"),
            embedding_dimensions=overrides.get("embedding_dimensions", 1024),
            embedding_batch_size=overrides.get("embedding_batch_size", 16),
            embedding_normalize=overrides.get("embedding_normalize", True),
        )

    def build_chunk(self, **overrides):
        return Chunk(
            id=overrides.get("id", 10),
            organization_id=overrides.get("organization_id", 7),
            knowledge_base_id=overrides.get("knowledge_base_id", 1),
            document_id=overrides.get("document_id", 2),
            knowledge_item_id=overrides.get("knowledge_item_id", 3),
            chunk_index=overrides.get("chunk_index", 0),
            content=overrides.get("content", "知识内容"),
            vector_id=overrides.get("vector_id"),
            metadata_json=overrides.get(
                "metadata_json",
                json.dumps(
                    {
                        "heading_path": ["员工手册", "提交流程"],
                        "page_start": 1,
                        "page_end": 2,
                        "file_type": "pdf",
                        "filename": "policy.pdf",
                        "permission_group": ["finance", "hr"],
                    },
                    ensure_ascii=False,
                ),
            ),
        )

    def test_build_stable_vector_id_is_deterministic(self) -> None:
        first = self.build_chunk()
        second = self.build_chunk(id=999)

        self.assertEqual(
            vector_service.build_stable_vector_id(first),
            vector_service.build_stable_vector_id(second),
        )

    def test_sanitize_metadata_for_elasticsearch_flattens_complex_values(self) -> None:
        sanitized = vector_service.sanitize_metadata_for_elasticsearch(
            {
                "title": "员工手册",
                "heading_path": ["员工手册", "提交流程"],
                "extra": {"page": 1},
                "enabled": True,
                "none_value": None,
            }
        )

        self.assertEqual(sanitized["title"], "员工手册")
        self.assertEqual(sanitized["enabled"], True)
        self.assertEqual(sanitized["heading_path"], ["员工手册", "提交流程"])
        self.assertEqual(sanitized["extra"], json.dumps({"page": 1}, ensure_ascii=False, sort_keys=True))
        self.assertNotIn("none_value", sanitized)

    def test_add_chunks_indexes_embeddings_and_metadata(self) -> None:
        fake_client = FakeElasticsearchClient()
        bulk_calls = []

        original_get_client = vector_service.get_elasticsearch_client
        original_get_embedding_model = vector_service.get_embedding_model
        original_get_settings = vector_service.get_settings
        original_execute_bulk_actions = vector_service.execute_bulk_actions
        try:
            vector_service.get_elasticsearch_client = lambda: fake_client
            vector_service.get_embedding_model = lambda: FakeEmbeddingModel()
            vector_service.get_settings = lambda: self.build_settings()
            vector_service.execute_bulk_actions = (
                lambda client, actions, refresh: bulk_calls.append(
                    {
                        "client": client,
                        "refresh": refresh,
                        "actions": list(actions),
                    }
                )
            )

            chunks = [
                self.build_chunk(id=1, chunk_index=0, content="第一段"),
                self.build_chunk(id=2, chunk_index=1, content="第二段"),
            ]
            result = vector_service.add_chunks(chunks)
        finally:
            vector_service.get_elasticsearch_client = original_get_client
            vector_service.get_embedding_model = original_get_embedding_model
            vector_service.get_settings = original_get_settings
            vector_service.execute_bulk_actions = original_execute_bulk_actions

        self.assertEqual(result.index_name, "knowledge_chunks_v2_1")
        self.assertEqual(len(result.vector_ids), 2)
        self.assertEqual(len(fake_client.indices.created), 1)
        self.assertEqual(len(bulk_calls), 1)
        self.assertEqual(bulk_calls[0]["refresh"], "wait_for")
        self.assertEqual(len(bulk_calls[0]["actions"]), 2)
        self.assertEqual(bulk_calls[0]["actions"][0]["_index"], "knowledge_chunks_v2_1")
        self.assertEqual(bulk_calls[0]["actions"][0]["content"], "第一段")
        self.assertEqual(len(bulk_calls[0]["actions"][0]["embedding"]), 1024)
        self.assertEqual(bulk_calls[0]["actions"][0]["embedding"][0], 3.0)
        self.assertEqual(bulk_calls[0]["actions"][0]["metadata"]["chunk_index"], 0)
        self.assertEqual(bulk_calls[0]["actions"][0]["file_type"], "pdf")
        self.assertEqual(bulk_calls[0]["actions"][0]["source_file"], "policy.pdf")
        self.assertEqual(bulk_calls[0]["actions"][0]["heading_path"], ["员工手册", "提交流程"])
        self.assertEqual(
            bulk_calls[0]["actions"][0]["permission_group"],
            ["finance", "hr"],
        )
        self.assertEqual(
            fake_client.indices.created[0]["mappings"]["properties"]["content"]["analyzer"],
            "cjk",
        )

    def test_delete_vectors_calls_elasticsearch_delete(self) -> None:
        fake_client = FakeElasticsearchClient()
        original_get_client = vector_service.get_elasticsearch_client
        original_get_settings = vector_service.get_settings
        try:
            vector_service.get_elasticsearch_client = lambda: fake_client
            vector_service.get_settings = lambda: self.build_settings(
                elasticsearch_write_refresh="wait_for"
            )
            vector_service.delete_vectors(knowledge_base_id=7, vector_ids=["v1", "v2"])
        finally:
            vector_service.get_elasticsearch_client = original_get_client
            vector_service.get_settings = original_get_settings

        self.assertEqual(
            fake_client.last_delete_ids,
            [
                {"index": "knowledge_chunks_7_active", "id": "v1", "refresh": "wait_for"},
                {"index": "knowledge_chunks_7_active", "id": "v2", "refresh": "wait_for"},
            ],
        )

    def test_search_similar_chunks_returns_hits(self) -> None:
        fake_client = FakeElasticsearchClient()
        fake_client.indices.exists_result = True
        fake_client.search_response = {
            "hits": {
                "hits": [
                    {
                        "_id": "vector_1",
                        "_score": 0.91,
                        "_source": {
                            "vector_id": "vector_1",
                            "chunk_id": 11,
                            "document_id": 2,
                            "knowledge_item_id": 3,
                            "content": "报销流程需要先提交发票。",
                            "metadata": {
                                "filename": "policy.txt",
                                "heading_path": ["差旅制度"],
                            },
                        },
                    }
                ]
            }
        }

        original_get_client = vector_service.get_elasticsearch_client
        original_get_embedding_model = vector_service.get_embedding_model
        original_get_settings = vector_service.get_settings
        try:
            vector_service.get_elasticsearch_client = lambda: fake_client
            vector_service.get_embedding_model = lambda: FakeEmbeddingModel()
            vector_service.get_settings = lambda: self.build_settings()
            hits = vector_service.search_similar_chunks(
                organization_id=7,
                knowledge_base_id=1,
                query="报销流程",
                top_k=3,
            )
        finally:
            vector_service.get_elasticsearch_client = original_get_client
            vector_service.get_embedding_model = original_get_embedding_model
            vector_service.get_settings = original_get_settings

        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].chunk_id, 11)
        self.assertEqual(hits[0].document_id, 2)
        self.assertEqual(hits[0].knowledge_item_id, 3)
        self.assertEqual(hits[0].score, 0.91)
        self.assertEqual(fake_client.last_search["index"], "knowledge_chunks_1_active")
        self.assertEqual(fake_client.last_search["size"], 3)
        self.assertEqual(len(fake_client.last_search["knn"]["query_vector"]), 1024)
        self.assertEqual(fake_client.last_search["knn"]["query_vector"][0], 4.0)
        self.assertEqual(
            fake_client.last_search["knn"]["filter"],
            [
                {"term": {"organization_id": 7}},
                {"term": {"knowledge_base_id": 1}},
            ],
        )

    def test_search_bm25_chunks_uses_the_same_organization_and_knowledge_base_filters(self) -> None:
        fake_client = FakeElasticsearchClient()
        fake_client.indices.exists_result = True
        fake_client.search_response = {
            "hits": {
                "hits": [
                    {
                        "_id": "vector_2",
                        "_score": 8.2,
                        "_source": {
                            "vector_id": "vector_2",
                            "chunk_id": 12,
                            "document_id": 2,
                            "knowledge_item_id": 3,
                            "content": "采购复核触发条件是金额超过二十万元。",
                            "metadata": {},
                        },
                    }
                ]
            }
        }

        original_get_client = vector_service.get_elasticsearch_client
        original_get_settings = vector_service.get_settings
        try:
            vector_service.get_elasticsearch_client = lambda: fake_client
            vector_service.get_settings = lambda: self.build_settings()
            hits = vector_service.search_bm25_chunks(
                organization_id=7,
                knowledge_base_id=1,
                query="采购复核",
                top_k=3,
            )
        finally:
            vector_service.get_elasticsearch_client = original_get_client
            vector_service.get_settings = original_get_settings

        self.assertEqual(hits[0].retrieval_sources, ("bm25",))
        self.assertEqual(hits[0].bm25_score, 8.2)
        self.assertEqual(
            fake_client.last_search["query"]["bool"]["filter"],
            [
                {"term": {"organization_id": 7}},
                {"term": {"knowledge_base_id": 1}},
            ],
        )

    def test_parse_chunk_metadata_returns_empty_dict_on_invalid_json(self) -> None:
        self.assertEqual(vector_service.parse_chunk_metadata("{bad json"), {})

    def test_validate_embedding_dimensions_raises_clear_error(self) -> None:
        original_get_settings = vector_service.get_settings
        try:
            vector_service.get_settings = lambda: self.build_settings(embedding_dimensions=3)
            with self.assertRaises(ValueError) as context:
                vector_service.validate_embedding_dimensions([[1.0, 2.0]])
        finally:
            vector_service.get_settings = original_get_settings

        self.assertIn("expected 3, got 2", str(context.exception))

    def test_get_write_refresh_option_parses_values(self) -> None:
        original_get_settings = vector_service.get_settings
        try:
            vector_service.get_settings = lambda: self.build_settings(
                elasticsearch_write_refresh="false"
            )
            self.assertEqual(vector_service.get_write_refresh_option(), False)

            vector_service.get_settings = lambda: self.build_settings(
                elasticsearch_write_refresh="true"
            )
            self.assertEqual(vector_service.get_write_refresh_option(), True)

            vector_service.get_settings = lambda: self.build_settings(
                elasticsearch_write_refresh="wait_for"
            )
            self.assertEqual(vector_service.get_write_refresh_option(), "wait_for")
        finally:
            vector_service.get_settings = original_get_settings
