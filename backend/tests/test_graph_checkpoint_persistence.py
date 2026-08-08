"""PostgreSQL LangGraph checkpoint 的持久化回归测试。"""

import sys
import unittest
from pathlib import Path

from sqlmodel import Session

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from app.config import Settings
from app.graph import nodes
from app.graph.checkpointer import (
    close_graph_checkpointer,
    initialize_graph_checkpointer,
    setup_graph_checkpoint_schema,
)
from app.graph.langgraph_workflow import (
    build_checkpointed_workflow,
    get_checkpoint_snapshot_with_graph,
    invoke_graph,
    resume_graph,
)
from postgres_test_utils import PostgresTestDatabase


class GraphCheckpointPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.test_database = PostgresTestDatabase()
        self.engine = self.test_database.create_engine()
        self.session = Session(self.engine)
        self.settings = Settings(
            database_url=self.test_database.database_url,
            graph_checkpoint_database_url=self.test_database.database_url,
            graph_checkpoint_pool_min_size=1,
            graph_checkpoint_pool_max_size=2,
        )
        setup_graph_checkpoint_schema(self.settings)
        initialize_graph_checkpointer(self.settings)

    def tearDown(self) -> None:
        close_graph_checkpointer()
        self.session.close()
        self.test_database.dispose()

    def test_interrupt_survives_rebuilding_graph_and_can_resume(self) -> None:
        original_router = nodes.router_node
        original_retrieve = nodes.rag_service.retrieve
        original_route_llm = nodes.llm_router_service.route_question_with_llm
        try:
            # 固定走 RAG 且返回空证据，稳定触发 human_review interrupt。
            nodes.router_node = lambda state: {
                **state,
                "route": "rag",
                "route_reason": "test route",
                "node_trace": ["START", "router"],
            }
            nodes.rag_service.retrieve = lambda *args, **kwargs: []
            nodes.llm_router_service.route_question_with_llm = lambda *args, **kwargs: None

            initial_graph = build_checkpointed_workflow(self.session)
            invoke_graph(
                initial_graph,
                state={
                    "question": "测试人工审核恢复",
                    "organization_id": 7,
                    "knowledge_base_id": 8,
                    "conversation_id": 9,
                    "thread_id": "persistent-thread",
                },
                thread_id="persistent-thread",
            )
            first_snapshot = get_checkpoint_snapshot_with_graph(
                initial_graph,
                "persistent-thread",
            )
            self.assertTrue(first_snapshot.interrupts)
            self.assertEqual(first_snapshot.values["conversation_id"], 9)

            # 关键断言：不复用旧 graph 对象，重新 compile 后仍能从 PostgreSQL 读到暂停点。
            del initial_graph
            rebuilt_graph = build_checkpointed_workflow(self.session)
            recovered_snapshot = get_checkpoint_snapshot_with_graph(
                rebuilt_graph,
                "persistent-thread",
            )
            self.assertTrue(recovered_snapshot.interrupts)
            self.assertEqual(recovered_snapshot.values["organization_id"], 7)

            resume_graph(
                rebuilt_graph,
                thread_id="persistent-thread",
                approved=False,
                human_note="证据不足",
            )
            final_snapshot = get_checkpoint_snapshot_with_graph(
                rebuilt_graph,
                "persistent-thread",
            )
        finally:
            nodes.router_node = original_router
            nodes.rag_service.retrieve = original_retrieve
            nodes.llm_router_service.route_question_with_llm = original_route_llm

        self.assertFalse(final_snapshot.interrupts)
        self.assertEqual(final_snapshot.values["relevance_decision"], "rejected_by_human")
        self.assertIn("人工复核未通过", final_snapshot.values["answer"])


if __name__ == "__main__":
    unittest.main()
