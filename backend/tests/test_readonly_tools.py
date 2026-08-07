import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

from sqlmodel import Session

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from app.agent_tools.registry import (
    build_openai_tool_definitions,
    execute_readonly_tool,
    plan_readonly_tool,
)
from app.agent_tools.schemas import (
    ToolCallRequest,
    ToolExecutionContext,
)
from app.db.models import Chunk, Conversation, Document, KnowledgeBase, KnowledgeItem, Message
from app.graph import nodes
from app.services.rag_service import RetrievedDocument
from postgres_test_utils import PostgresTestDatabase
from resource_authorization_utils import create_test_identity


class ReadonlyToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.test_database = PostgresTestDatabase()
        self.engine = self.test_database.create_engine()
        self.session = Session(self.engine)
        self.principal = create_test_identity(self.session)
        self.knowledge_base = KnowledgeBase(
            name="工具测试知识库",
            description="只读工具测试",
            organization_id=self.principal.organization_id,
            created_by_user_id=self.principal.user_id,
        )
        self.session.add(self.knowledge_base)
        self.session.commit()
        self.session.refresh(self.knowledge_base)
        self.conversation = Conversation(
            organization_id=self.principal.organization_id,
            created_by_user_id=self.principal.user_id,
            knowledge_base_id=self.knowledge_base.id,
            title="历史恢复测试",
            thread_id="history-tool-test",
        )
        self.session.add(self.conversation)
        self.session.commit()
        self.session.refresh(self.conversation)
        self.session.add_all(
            [
                Message(
                    conversation_id=self.conversation.id,
                    role="user",
                    content="之前确认过采购复核金额超过二十万元需要委员会复核。",
                ),
                Message(
                    conversation_id=self.conversation.id,
                    role="assistant",
                    content="已记录采购复核的金额门槛。",
                ),
            ]
        )
        self.session.commit()

        self.document = Document(
            organization_id=self.principal.organization_id,
            created_by_user_id=self.principal.user_id,
            knowledge_base_id=self.knowledge_base.id,
            filename="policy.txt",
            file_path="raw/dev/policy.txt",
            file_type="txt",
            extracted_text="第一段正文。第二段正文。第三段正文。",
        )
        self.item = KnowledgeItem(
            organization_id=self.principal.organization_id,
            created_by_user_id=self.principal.user_id,
            knowledge_base_id=self.knowledge_base.id,
            title="采购制度",
            content="采购制度正文",
            status="active",
            source_type="document",
            source_document_id=None,
        )
        self.session.add(self.document)
        self.session.add(self.item)
        self.session.commit()
        self.session.refresh(self.document)
        self.session.refresh(self.item)

        # Chunk 通过 knowledge_item_id 归属知识条目；文档工具仍单独按 document 查询。
        self.item.source_document_id = self.document.id
        self.session.add(self.item)
        self.chunks = []
        for index, content in enumerate(("前一段", "中心段，包含审批条件", "后一段")):
            chunk = Chunk(
                organization_id=self.principal.organization_id,
                knowledge_base_id=self.knowledge_base.id,
                document_id=self.document.id,
                knowledge_item_id=self.item.id,
                chunk_index=index,
                content=content,
                metadata_json='{"filename":"policy.txt"}',
            )
            self.session.add(chunk)
            self.chunks.append(chunk)
        self.session.commit()
        for chunk in self.chunks:
            self.session.refresh(chunk)

    def tearDown(self) -> None:
        self.session.close()
        self.test_database.dispose()

    def test_get_document_enforces_organization_and_returns_content(self) -> None:
        result = execute_readonly_tool(
            ToolCallRequest(
                name="get_document",
                arguments={"document_id": self.document.id},
            ),
            context=ToolExecutionContext(
                organization_id=self.principal.organization_id,
                knowledge_base_id=self.knowledge_base.id,
            ),
            session=self.session,
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.data["filename"], "policy.txt")
        self.assertIn("第一段正文", result.data["content"])
        self.assertEqual(result.citations[0]["doc_id"], self.document.id)

    def test_openai_tool_definitions_expose_registered_readonly_tools(self) -> None:
        definitions = build_openai_tool_definitions()
        names = {
            item["function"]["name"]
            for item in definitions
            if isinstance(item, dict)
        }
        self.assertIn("get_document", names)
        self.assertIn("get_chunk_neighbors", names)
        self.assertIn("list_knowledge_base_documents", names)
        for item in definitions:
            self.assertEqual(item["type"], "function")
            self.assertIn("parameters", item["function"])

    def test_wrong_organization_cannot_read_document(self) -> None:
        result = execute_readonly_tool(
            ToolCallRequest(
                name="get_document",
                arguments={"document_id": self.document.id},
            ),
            context=ToolExecutionContext(
                organization_id=self.principal.organization_id + 100,
                knowledge_base_id=self.knowledge_base.id,
            ),
            session=self.session,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "not_found")

    def test_invalid_arguments_do_not_query_database(self) -> None:
        session = Mock()
        result = execute_readonly_tool(
            ToolCallRequest(
                name="get_chunk_neighbors",
                arguments={"chunk_id": "not-an-id", "radius": 99},
            ),
            context=ToolExecutionContext(
                organization_id=self.principal.organization_id,
                knowledge_base_id=self.knowledge_base.id,
            ),
            session=session,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "invalid_arguments")
        session.exec.assert_not_called()

    def test_get_chunk_neighbors_returns_ordered_context(self) -> None:
        result = execute_readonly_tool(
            ToolCallRequest(
                name="get_chunk_neighbors",
                arguments={"chunk_id": self.chunks[1].id, "radius": 1},
            ),
            context=ToolExecutionContext(
                organization_id=self.principal.organization_id,
                knowledge_base_id=self.knowledge_base.id,
            ),
            session=self.session,
        )

        self.assertTrue(result.ok)
        self.assertEqual(
            [item["chunk_index"] for item in result.data["chunks"]],
            [0, 1, 2],
        )
        self.assertEqual(len(result.citations), 3)

    def test_unknown_tool_is_structured_error(self) -> None:
        result = execute_readonly_tool(
            ToolCallRequest(name="run_sql", arguments={"sql": "select 1"}),
            context=ToolExecutionContext(
                organization_id=self.principal.organization_id,
                knowledge_base_id=self.knowledge_base.id,
            ),
            session=self.session,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "unknown_tool")

    def test_planner_selects_readonly_tool_from_question(self) -> None:
        documents = [
            RetrievedDocument(
                doc_id=7,
                chunk_id=8,
                knowledge_item_id=9,
                title="制度",
                content="正文",
                score=0.9,
                metadata={},
            )
        ]
        request = plan_readonly_tool("请给我这份文档的完整原文", documents)
        self.assertIsNotNone(request)
        self.assertEqual(request.name, "get_document")
        self.assertEqual(request.arguments["document_id"], 7)

        neighbor_request = plan_readonly_tool("请补充上一段和下一段上下文", documents)
        self.assertEqual(neighbor_request.name, "get_chunk_neighbors")

    def test_tool_node_puts_result_into_context_and_citations(self) -> None:
        state = nodes.tool_decision_node(
            {
                "question": "请给我这份文档的完整原文",
                "organization_id": self.principal.organization_id,
                "knowledge_base_id": self.knowledge_base.id,
                "retrieved_docs": [
                    RetrievedDocument(
                        doc_id=self.document.id,
                        chunk_id=self.chunks[0].id,
                        knowledge_item_id=self.item.id,
                        title="policy.txt",
                        content="第一段正文",
                        score=0.9,
                        metadata={},
                    )
                ],
            }
        )
        state = nodes.tool_call_node(state, self.session)
        self.assertTrue(state["tool_used"])
        self.assertEqual(state["tool_results"][0]["tool_name"], "get_document")
        self.assertEqual(state["tool_citations"][0]["doc_id"], self.document.id)

    def test_history_tool_is_scoped_to_current_conversation(self) -> None:
        result = execute_readonly_tool(
            ToolCallRequest(
                name="search_conversation_history",
                arguments={"query": "采购复核金额", "limit": 5},
            ),
            context=ToolExecutionContext(
                organization_id=self.principal.organization_id,
                knowledge_base_id=self.knowledge_base.id,
                user_id=self.principal.user_id,
                role="viewer",
                conversation_id=self.conversation.id,
            ),
            session=self.session,
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.data["conversation_id"], self.conversation.id)
        self.assertTrue(result.data["messages"])
        self.assertIn("采购复核", result.data["messages"][0]["content"])

    def test_history_tool_requires_user_and_conversation_scope(self) -> None:
        result = execute_readonly_tool(
            ToolCallRequest(
                name="search_conversation_history",
                arguments={"query": "采购复核"},
            ),
            context=ToolExecutionContext(
                organization_id=self.principal.organization_id,
                knowledge_base_id=self.knowledge_base.id,
                role="viewer",
            ),
            session=self.session,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "forbidden")


if __name__ == "__main__":
    unittest.main()
