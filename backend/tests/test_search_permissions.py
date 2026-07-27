import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
for path in (BACKEND_DIR, TESTS_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from app.api import search as search_api
from app.services.vector_service import SemanticSearchHit
from resource_authorization_utils import ResourceAuthorizationTestCase


class SearchPermissionTests(ResourceAuthorizationTestCase, unittest.TestCase):
    def setUp(self) -> None:
        self.setUp_resource_authorization()

    def tearDown(self) -> None:
        self.tearDown_resource_authorization()

    def test_search_rejects_another_organization_knowledge_base_before_es(self) -> None:
        called = False
        original_search = search_api.search_similar_chunks
        try:
            def fail_if_called(*args, **kwargs):
                nonlocal called
                called = True
                return []

            search_api.search_similar_chunks = fail_if_called
            response = self.client.post(
                "/search/semantic",
                json={"knowledge_base_id": self.knowledge_base_b_id, "query": "private"},
            )
        finally:
            search_api.search_similar_chunks = original_search

        self.assertEqual(response.status_code, 404)
        self.assertFalse(called)

    def test_search_passes_organization_filter_to_vector_service(self) -> None:
        captured = {}
        original_search = search_api.search_similar_chunks
        try:
            def fake_search(organization_id, knowledge_base_id, query, *, top_k):
                captured.update(
                    organization_id=organization_id,
                    knowledge_base_id=knowledge_base_id,
                    query=query,
                    top_k=top_k,
                )
                return [
                    SemanticSearchHit(
                        vector_id="chunk-a",
                        chunk_id=1,
                        document_id=None,
                        knowledge_item_id=None,
                        content="Organization A evidence",
                        score=0.99,
                        metadata={},
                    )
                ]

            search_api.search_similar_chunks = fake_search
            response = self.client.post(
                "/search/semantic",
                json={"knowledge_base_id": self.knowledge_base_a_id, "query": "evidence"},
            )
        finally:
            search_api.search_similar_chunks = original_search

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["organization_id"], self.organization_a_id)
        self.assertEqual(captured["knowledge_base_id"], self.knowledge_base_a_id)


if __name__ == "__main__":
    unittest.main()
