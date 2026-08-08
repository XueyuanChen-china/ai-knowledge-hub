import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

from sqlmodel import Session, select

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from app.agent_tools.authorization import authorize_tool_call
from app.agent_tools.registry import execute_readonly_tool
from app.agent_tools.schemas import ToolCallRequest, ToolExecutionContext
from app.db.models import SecurityAuditLog
from app.graph import nodes
from postgres_test_utils import PostgresTestDatabase
from resource_authorization_utils import create_test_identity


class ToolAuthorizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = PostgresTestDatabase()
        self.engine = self.database.create_engine()
        self.session = Session(self.engine)
        self.principal = create_test_identity(self.session)
        self.context = ToolExecutionContext(
            organization_id=self.principal.organization_id,
            knowledge_base_id=1,
            user_id=self.principal.user_id,
            role="viewer",
            conversation_id=10,
        )

    def tearDown(self) -> None:
        self.session.close()
        self.database.dispose()

    def test_viewer_can_use_read_tools_but_unknown_write_tool_is_denied(self) -> None:
        read_request = ToolCallRequest(
            name="get_document",
            arguments={"document_id": 1},
        )
        decision = authorize_tool_call(read_request, self.context)
        self.assertTrue(decision.allowed)

        write_request = ToolCallRequest(
            name="delete_knowledge_base",
            arguments={"knowledge_base_id": 1},
        )
        decision = authorize_tool_call(write_request, self.context)
        self.assertFalse(decision.allowed)

        result = execute_readonly_tool(
            write_request,
            context=self.context,
            session=self.session,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "unknown_tool")

    def test_unknown_role_is_denied_before_database_handler(self) -> None:
        session = Mock()
        context = self.context.model_copy(update={"role": "unknown"})
        result = execute_readonly_tool(
            ToolCallRequest(
                name="get_document",
                arguments={"document_id": 1},
            ),
            context=context,
            session=session,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "forbidden")
        session.exec.assert_not_called()

    def test_tool_call_is_audited_without_storing_query_text(self) -> None:
        result = execute_readonly_tool(
            ToolCallRequest(
                name="search_knowledge_base",
                arguments={"query": "高度敏感的内部问题", "top_k": 5},
            ),
            context=self.context,
            session=self.session,
        )
        # 该测试库没有 ES 结果，工具失败也必须留下可审计记录。
        self.assertFalse(result.ok)
        logs = self.session.exec(
            select(SecurityAuditLog).where(SecurityAuditLog.action == "agent_tool_call")
        ).all()
        self.assertTrue(logs)
        details = json.loads(logs[-1].details_json)
        self.assertEqual(details["arguments"]["query"], "<omitted>")
        self.assertNotIn("高度敏感的内部问题", logs[-1].details_json)

    def test_tool_call_limit_returns_structured_error(self) -> None:
        state = nodes.tool_call_node(
            {
                "tool_call": {
                    "name": "get_document",
                    "arguments": {"document_id": 1},
                    "reason": "test",
                },
                "organization_id": self.principal.organization_id,
                "knowledge_base_id": 1,
                "conversation_id": 10,
                "user_id": self.principal.user_id,
                "role": "viewer",
                "tool_call_count": 1,
            },
            self.session,
        )
        self.assertEqual(state["tool_results"][0]["error_code"], "tool_call_limit")
        self.assertEqual(state["tool_call_count"], 1)

