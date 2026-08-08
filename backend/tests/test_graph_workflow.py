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

from app.db.models import KnowledgeBase
from app.graph import nodes
import app.graph.workflow as graph_workflow
from app.graph.workflow import build_basic_workflow
from app.agent_tools.schemas import ToolCallRequest
from app.services import rag_service
from app.services.llm_router_service import RouterDecision
from app.services.rag_service import RetrievedDocument
from postgres_test_utils import PostgresTestDatabase
from resource_authorization_utils import create_test_identity


class GraphWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.test_database = PostgresTestDatabase()
        self.engine = self.test_database.create_engine()
        self.session = Session(self.engine)
        self.principal = create_test_identity(self.session)

        knowledge_base = KnowledgeBase(
            name="制度库",
            description="用于图工作流测试",
            organization_id=self.principal.organization_id,
            created_by_user_id=self.principal.user_id,
        )
        self.session.add(knowledge_base)
        self.session.commit()
        self.session.refresh(knowledge_base)
        self.knowledge_base = knowledge_base

    def tearDown(self) -> None:
        self.session.close()
        self.test_database.dispose()

    def test_direct_question_does_not_trigger_retrieve(self) -> None:
        workflow = build_basic_workflow()

        original_tool_call = graph_workflow.tool_call_node
        original_retrieve = nodes.rag_service.retrieve
        original_llm_route = nodes.llm_router_service.route_question_with_llm
        try:
            def fail_retrieve(*args, **kwargs):
                raise AssertionError("direct route should not call retrieve")

            nodes.rag_service.retrieve = fail_retrieve
            nodes.llm_router_service.route_question_with_llm = lambda *args, **kwargs: None

            state = workflow.invoke(
                {
                    "question": "你好",
                    "knowledge_base_id": self.knowledge_base.id,
                },
                session=self.session,
            )
        finally:
            nodes.rag_service.retrieve = original_retrieve
            nodes.llm_router_service.route_question_with_llm = original_llm_route

        self.assertEqual(state["route"], "direct")
        self.assertEqual(state["retrieved_docs"], [])
        self.assertEqual(state["context"], "")
        self.assertEqual(state["docs_preview"], "")
        self.assertEqual(state["citations"], [])
        self.assertIn("direct", state["node_trace"])
        self.assertIn("END", state["node_trace"])
        self.assertTrue(state["answer"])

    def test_rag_question_enters_retrieve(self) -> None:
        workflow = build_basic_workflow(retrieve_top_k=3)

        captured = {}
        original_retrieve = nodes.rag_service.retrieve
        original_llm_route = nodes.llm_router_service.route_question_with_llm
        original_answer_generate = nodes.llm_answer_service.generate_answer
        try:
            def fake_retrieve(question, knowledge_base_id, session, *, top_k=5):
                captured["question"] = question
                captured["knowledge_base_id"] = knowledge_base_id
                captured["top_k"] = top_k
                return [
                    RetrievedDocument(
                        doc_id=10,
                        chunk_id=20,
                        knowledge_item_id=30,
                        title="采购制度",
                        content="单次采购金额超过二十万元，需要采购委员会复核。",
                        score=0.91,
                        metadata={"heading_path": ["采购制度", "采购复核"]},
                    )
                ]

            nodes.rag_service.retrieve = fake_retrieve
            nodes.llm_router_service.route_question_with_llm = lambda *args, **kwargs: None
            nodes.llm_answer_service.generate_answer = (
                lambda question, documents, **kwargs: rag_service.RagAnswerResult(
                    answer="采购复核的触发条件是单次采购金额超过二十万元。\\n\\n参考来源：[1]",
                    context=rag_service.format_context(documents),
                    citations=rag_service.build_citations(documents),
                    used_fallback=False,
                )
            )

            state = workflow.invoke(
                {
                    "question": "采购复核的触发条件是什么？",
                    "knowledge_base_id": self.knowledge_base.id,
                },
                session=self.session,
            )
        finally:
            nodes.rag_service.retrieve = original_retrieve
            nodes.llm_router_service.route_question_with_llm = original_llm_route
            nodes.llm_answer_service.generate_answer = original_answer_generate

        self.assertEqual(state["route"], "rag")
        self.assertEqual(captured["question"], "采购复核的触发条件是什么？")
        self.assertEqual(captured["knowledge_base_id"], self.knowledge_base.id)
        self.assertEqual(captured["top_k"], 3)
        self.assertEqual(len(state["retrieved_docs"]), 1)
        self.assertEqual(state["retrieval_hit_count"], 1)
        self.assertIn("标题：采购制度", state["context"])
        self.assertIn("采购制度", state["docs_preview"])
        self.assertIn("单次采购金额超过二十万元", state["docs_preview"])
        self.assertIn("采购复核的触发条件是单次采购金额超过二十万元", state["answer"])
        self.assertFalse(state["answer_used_fallback"])
        self.assertEqual(state["relevance_decision"], "confident")
        self.assertEqual(state["review_reason"], "")
        self.assertEqual(len(state["citations"]), 1)
        self.assertEqual(state["citations"][0]["chunk_id"], 20)
        self.assertIn("retrieve", state["node_trace"])
        self.assertIn("relevance_check", state["node_trace"])
        self.assertIn("answer", state["node_trace"])
        self.assertIn("END", state["node_trace"])
        self.assertFalse(state["need_human_review"])

    def test_complex_question_does_not_trigger_retrieve(self) -> None:
        workflow = build_basic_workflow()

        original_retrieve = nodes.rag_service.retrieve
        original_llm_route = nodes.llm_router_service.route_question_with_llm
        try:
            def fail_retrieve(*args, **kwargs):
                raise AssertionError("complex route should not call retrieve in day 16")

            nodes.rag_service.retrieve = fail_retrieve
            nodes.llm_router_service.route_question_with_llm = lambda *args, **kwargs: None

            state = workflow.invoke(
                {
                    "question": "总结这个知识库的重点",
                    "knowledge_base_id": self.knowledge_base.id,
                },
                session=self.session,
            )
        finally:
            nodes.rag_service.retrieve = original_retrieve
            nodes.llm_router_service.route_question_with_llm = original_llm_route

        self.assertEqual(state["route"], "complex")
        self.assertEqual(state["retrieved_docs"], [])
        self.assertEqual(state["context"], "")
        self.assertEqual(state["docs_preview"], "")
        self.assertEqual(state["citations"], [])
        self.assertIn("complex", state["node_trace"])
        self.assertIn("END", state["node_trace"])
        self.assertTrue(state["answer"])

    def test_llm_router_output_is_used_before_rule_fallback(self) -> None:
        workflow = build_basic_workflow()

        original_retrieve = nodes.rag_service.retrieve
        original_llm_route = nodes.llm_router_service.route_question_with_llm
        try:
            def fail_retrieve(*args, **kwargs):
                raise AssertionError("llm-forced direct route should not call retrieve")

            nodes.rag_service.retrieve = fail_retrieve
            nodes.llm_router_service.route_question_with_llm = (
                lambda *args, **kwargs: RouterDecision(
                    route="direct",
                    reason="mock llm router",
                    raw_output='{"route":"direct"}',
                )
            )

            state = workflow.invoke(
                {
                    "question": "公司制度怎么报销",
                    "knowledge_base_id": self.knowledge_base.id,
                },
                session=self.session,
            )
        finally:
            nodes.rag_service.retrieve = original_retrieve
            nodes.llm_router_service.route_question_with_llm = original_llm_route

        self.assertEqual(state["route"], "direct")
        self.assertEqual(state["route_reason"], "mock llm router")

    def test_follow_up_tool_route_skips_retrieval(self) -> None:
        workflow = build_basic_workflow()
        original_llm_route = nodes.llm_router_service.route_question_with_llm
        original_tool_planner = nodes.plan_readonly_tool_with_llm
        original_tool_call = graph_workflow.tool_call_node
        original_retrieve = nodes.rag_service.retrieve
        try:
            nodes.llm_router_service.route_question_with_llm = lambda *args, **kwargs: RouterDecision(
                route="tool",
                reason="上一轮引用需要展开",
                raw_output='{"route":"tool"}',
            )
            nodes.plan_readonly_tool_with_llm = lambda *args, **kwargs: ToolCallRequest(
                name="get_document",
                arguments={"document_id": 123},
                reason="native tool call",
            )

            def fail_retrieve(*args, **kwargs):
                raise AssertionError("tool follow-up must not retrieve again")

            nodes.rag_service.retrieve = fail_retrieve

            def fake_tool_call(state, session):
                updated = dict(state)
                updated["tool_results"] = [{"ok": True, "data": {"content": "原文"}}]
                updated["tool_citations"] = [{"doc_id": 123}]
                updated["tool_error"] = ""
                updated["tool_call_count"] = 1
                return updated

            graph_workflow.tool_call_node = fake_tool_call

            state = workflow.invoke(
                {
                    "question": "展开刚才命中的文档",
                    "knowledge_base_id": self.knowledge_base.id,
                    "route": "tool",
                    "previous_citations": [{"doc_id": 123}],
                },
                session=self.session,
            )
        finally:
            nodes.llm_router_service.route_question_with_llm = original_llm_route
            nodes.plan_readonly_tool_with_llm = original_tool_planner
            graph_workflow.tool_call_node = original_tool_call
            nodes.rag_service.retrieve = original_retrieve

        self.assertEqual(state["route"], "tool")
        self.assertTrue(state["tool_results"])
        self.assertIn("answer", state)

    def test_retrieve_node_marks_need_human_review_when_no_docs(self) -> None:
        original_retrieve = nodes.rag_service.retrieve
        try:
            nodes.rag_service.retrieve = lambda *args, **kwargs: []

            state = nodes.retrieve_node(
                {
                    "question": "一个根本搜不到的问题",
                    "knowledge_base_id": self.knowledge_base.id,
                    "route": "rag",
                },
                session=self.session,
                top_k=5,
            )
        finally:
            nodes.rag_service.retrieve = original_retrieve

        self.assertEqual(state["retrieval_hit_count"], 0)
        self.assertEqual(state["context"], "")
        self.assertEqual(state["docs_preview"], "")
        self.assertEqual(state["citations"], [])
        self.assertIn("retrieve", state["node_trace"])

    def test_answer_node_falls_back_when_llm_answer_unavailable(self) -> None:
        original_generate = nodes.llm_answer_service.generate_answer
        try:
            nodes.llm_answer_service.generate_answer = (
                lambda question, documents, **kwargs: rag_service.RagAnswerResult(
                    answer="根据当前知识库检索结果，单次采购金额超过二十万元，需要采购委员会复核。",
                    context=rag_service.format_context(documents),
                    citations=rag_service.build_citations(documents),
                    used_fallback=True,
                )
            )

            state = nodes.answer_node(
                {
                    "question": "采购复核的触发条件是什么？",
                    "retrieved_docs": [
                        RetrievedDocument(
                            doc_id=10,
                            chunk_id=20,
                            knowledge_item_id=30,
                            title="采购制度",
                            content="单次采购金额超过二十万元，需要采购委员会复核。",
                            score=0.91,
                            metadata={},
                        )
                    ],
                    "context": "",
                    "node_trace": ["START", "router", "retrieve"],
                }
            )
        finally:
            nodes.llm_answer_service.generate_answer = original_generate

        self.assertIn("根据当前知识库检索结果", state["answer"])
        self.assertTrue(state["answer_used_fallback"])
        self.assertEqual(len(state["citations"]), 1)
        self.assertIn("answer", state["node_trace"])
        self.assertIn("END", state["node_trace"])

    def test_relevance_check_marks_need_review_when_no_docs(self) -> None:
        state = nodes.relevance_check_node(
            {
                "retrieved_docs": [],
                "retrieval_hit_count": 0,
                "relevance_score": 0.0,
                "node_trace": ["START", "router", "retrieve"],
            }
        )

        self.assertTrue(state["need_human_review"])
        self.assertEqual(state["relevance_decision"], "need_review")
        self.assertEqual(state["review_reason"], "no retrieved documents")
        self.assertIn("relevance_check", state["node_trace"])

    def test_relevance_check_marks_need_review_when_score_too_low(self) -> None:
        state = nodes.relevance_check_node(
            {
                "retrieved_docs": [
                    RetrievedDocument(
                        doc_id=10,
                        chunk_id=20,
                        knowledge_item_id=30,
                        title="采购制度",
                        content="和问题关系不大的内容。",
                        score=0.12,
                        metadata={},
                    )
                ],
                "retrieval_hit_count": 1,
                "relevance_score": 0.12,
                "node_trace": ["START", "router", "retrieve"],
            }
        )

        self.assertTrue(state["need_human_review"])
        self.assertEqual(state["relevance_decision"], "need_review")
        self.assertIn("below threshold", state["review_reason"])
        self.assertIn("relevance_check", state["node_trace"])

    def test_relevance_check_marks_need_review_when_critical_entity_is_missing(self) -> None:
        state = nodes.relevance_check_node(
            {
                "question": "R-001 风险由谁负责？",
                "retrieved_docs": [
                    RetrievedDocument(
                        doc_id=10,
                        chunk_id=20,
                        knowledge_item_id=30,
                        title="采购制度",
                        content="R-002 供应商交付延迟，由采购负责人跟进。",
                        score=0.91,
                        metadata={"heading_path": ["风险清单"]},
                    )
                ],
                "retrieval_hit_count": 1,
                "relevance_score": 0.91,
                "node_trace": ["START", "router", "retrieve"],
            }
        )

        self.assertTrue(state["need_human_review"])
        self.assertEqual(state["relevance_decision"], "need_review")
        self.assertEqual(
            state["review_reason"],
            "retrieved docs do not cover critical query entities: r-001",
        )
        self.assertIn("relevance_check", state["node_trace"])

    def test_relevance_check_uses_rerank_score_instead_of_small_rrf_score(self) -> None:
        state = nodes.relevance_check_node(
            {
                "question": "采购复核的金额门槛是多少？",
                "retrieved_docs": [
                    RetrievedDocument(
                        doc_id=10,
                        chunk_id=20,
                        knowledge_item_id=30,
                        title="采购制度",
                        content="单次采购金额超过二十万元，需要采购委员会复核。",
                        score=0.03,
                        metadata={
                            "rrf_score": 0.03,
                            "rerank_score": 0.94,
                            "retrieval_sources": ["dense", "bm25"],
                        },
                    )
                ],
                "retrieval_hit_count": 1,
                "relevance_score": 0.94,
                "node_trace": ["START", "router", "retrieve"],
            }
        )

        self.assertFalse(state["need_human_review"])
        self.assertEqual(state["relevance_decision"], "confident")

    def test_critical_entity_extraction_does_not_create_chinese_ngram_noise(self) -> None:
        self.assertEqual(
            nodes.extract_critical_query_entities("采购复核的触发条件是什么？"),
            [],
        )
        self.assertEqual(
            nodes.extract_critical_query_entities("R-001 超过 20 万元需要谁审批？"),
            ["r-001", "20万元"],
        )

    def test_workflow_does_not_answer_when_critical_entity_is_missing(self) -> None:
        workflow = build_basic_workflow(retrieve_top_k=3)

        original_retrieve = nodes.rag_service.retrieve
        original_llm_route = nodes.llm_router_service.route_question_with_llm
        original_answer_generate = nodes.llm_answer_service.generate_answer
        try:
            nodes.rag_service.retrieve = lambda *args, **kwargs: [
                RetrievedDocument(
                    doc_id=10,
                    chunk_id=20,
                    knowledge_item_id=30,
                    title="采购制度",
                    content="R-002 供应商交付延迟，由采购负责人跟进。",
                    score=0.91,
                    metadata={"heading_path": ["风险清单"]},
                )
            ]
            nodes.llm_router_service.route_question_with_llm = lambda *args, **kwargs: None

            def fail_answer(*args, **kwargs):
                raise AssertionError("unsupported retrieval should not call answer node")

            nodes.llm_answer_service.generate_answer = fail_answer

            state = workflow.invoke(
                {
                    "question": "R-001 风险由谁负责？",
                    "knowledge_base_id": self.knowledge_base.id,
                },
                session=self.session,
            )
        finally:
            nodes.rag_service.retrieve = original_retrieve
            nodes.llm_router_service.route_question_with_llm = original_llm_route
            nodes.llm_answer_service.generate_answer = original_answer_generate

        self.assertTrue(state["need_human_review"])
        self.assertEqual(state["relevance_decision"], "need_review")
        self.assertEqual(
            state["review_reason"],
            "retrieved docs do not cover critical query entities: r-001",
        )
        self.assertEqual(state["answer"], "当前检索结果不足以支持直接回答，需要人工复核。")
        self.assertNotIn("answer", state["node_trace"])

    def test_workflow_does_not_answer_when_retrieval_is_empty(self) -> None:
        workflow = build_basic_workflow(retrieve_top_k=3)

        original_retrieve = nodes.rag_service.retrieve
        original_llm_route = nodes.llm_router_service.route_question_with_llm
        original_answer_generate = nodes.llm_answer_service.generate_answer
        try:
            nodes.rag_service.retrieve = lambda *args, **kwargs: []
            nodes.llm_router_service.route_question_with_llm = lambda *args, **kwargs: None

            def fail_answer(*args, **kwargs):
                raise AssertionError("empty retrieval should not call answer node")

            nodes.llm_answer_service.generate_answer = fail_answer

            state = workflow.invoke(
                {
                    "question": "一个知识库里不存在的问题",
                    "knowledge_base_id": self.knowledge_base.id,
                },
                session=self.session,
            )
        finally:
            nodes.rag_service.retrieve = original_retrieve
            nodes.llm_router_service.route_question_with_llm = original_llm_route
            nodes.llm_answer_service.generate_answer = original_answer_generate

        self.assertTrue(state["need_human_review"])
        self.assertEqual(state["relevance_decision"], "need_review")
        self.assertEqual(state["review_reason"], "no retrieved documents")
        self.assertEqual(state["answer"], "当前检索结果不足以支持直接回答，需要人工复核。")
        self.assertNotIn("answer", state["node_trace"])
        self.assertIn("review", state["node_trace"])
        self.assertIn("END", state["node_trace"])

    def test_workflow_does_not_answer_when_top_score_too_low(self) -> None:
        workflow = build_basic_workflow(retrieve_top_k=3)

        original_retrieve = nodes.rag_service.retrieve
        original_llm_route = nodes.llm_router_service.route_question_with_llm
        original_answer_generate = nodes.llm_answer_service.generate_answer
        try:
            nodes.rag_service.retrieve = lambda *args, **kwargs: [
                RetrievedDocument(
                    doc_id=10,
                    chunk_id=20,
                    knowledge_item_id=30,
                    title="采购制度",
                    content="和问题关系不大的内容。",
                    score=0.12,
                    metadata={},
                )
            ]
            nodes.llm_router_service.route_question_with_llm = lambda *args, **kwargs: None

            def fail_answer(*args, **kwargs):
                raise AssertionError("low-confidence retrieval should not call answer node")

            nodes.llm_answer_service.generate_answer = fail_answer

            state = workflow.invoke(
                {
                    "question": "非常泛、命中不准的问题",
                    "knowledge_base_id": self.knowledge_base.id,
                },
                session=self.session,
            )
        finally:
            nodes.rag_service.retrieve = original_retrieve
            nodes.llm_router_service.route_question_with_llm = original_llm_route
            nodes.llm_answer_service.generate_answer = original_answer_generate

        self.assertTrue(state["need_human_review"])
        self.assertEqual(state["relevance_decision"], "need_review")
        self.assertIn("below threshold", state["review_reason"])
        self.assertEqual(state["answer"], "当前检索结果不足以支持直接回答，需要人工复核。")
        self.assertIn("review", state["node_trace"])
