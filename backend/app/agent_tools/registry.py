"""只读工具注册、规划、参数校验和执行入口。"""

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Type

from pydantic import BaseModel, ValidationError
from sqlmodel import Session

from app.agent_tools import conversation_tools, knowledge_tools
from app.agent_tools.audit import record_tool_call_audit
from app.agent_tools.authorization import authorize_tool_call
from app.agent_tools.schemas import (
    GetChunkNeighborsArgs,
    GetDocumentArgs,
    GetKnowledgeItemArgs,
    ListKnowledgeBaseDocumentsArgs,
    SearchKnowledgeBaseArgs,
    SearchConversationHistoryArgs,
    ToolCallRequest,
    ToolExecutionContext,
    ToolExecutionResult,
)
from app.config import get_settings
from app.observability.metrics import get_metrics


@dataclass(frozen=True)
class ReadOnlyToolDefinition:
    name: str
    description: str
    arguments_model: Type[BaseModel]
    handler: Callable[..., Any]


TOOL_DEFINITIONS: Dict[str, ReadOnlyToolDefinition] = {
    "search_knowledge_base": ReadOnlyToolDefinition(
        "search_knowledge_base",
        "在当前知识库内执行受控混合检索",
        SearchKnowledgeBaseArgs,
        knowledge_tools.search_knowledge_base,
    ),
    "get_document": ReadOnlyToolDefinition(
        "get_document",
        "读取当前知识库内一份文档的提取文本",
        GetDocumentArgs,
        knowledge_tools.get_document,
    ),
    "get_knowledge_item": ReadOnlyToolDefinition(
        "get_knowledge_item",
        "读取当前知识库内一条知识条目的正文和元数据",
        GetKnowledgeItemArgs,
        knowledge_tools.get_knowledge_item,
    ),
    "get_chunk_neighbors": ReadOnlyToolDefinition(
        "get_chunk_neighbors",
        "读取当前知识条目相邻的若干文本切片",
        GetChunkNeighborsArgs,
        knowledge_tools.get_chunk_neighbors,
    ),
    "list_knowledge_base_documents": ReadOnlyToolDefinition(
        "list_knowledge_base_documents",
        "列出当前知识库中的文档",
        ListKnowledgeBaseDocumentsArgs,
        knowledge_tools.list_knowledge_base_documents,
    ),
    "search_conversation_history": ReadOnlyToolDefinition(
        "search_conversation_history",
        "只在当前用户有权限的当前会话内搜索历史消息",
        SearchConversationHistoryArgs,
        conversation_tools.search_conversation_history,
    ),
}


def list_readonly_tools() -> List[Dict[str, str]]:
    return [
        {"name": definition.name, "description": definition.description}
        for definition in TOOL_DEFINITIONS.values()
    ]


def build_openai_tool_definitions() -> list[dict[str, Any]]:
    """把内部 Pydantic 参数协议转换成 OpenAI 兼容 tools 协议。"""

    definitions: list[dict[str, Any]] = []
    for definition in TOOL_DEFINITIONS.values():
        # 项目同时兼容 Pydantic 1/2；优先使用 Pydantic 2 的 JSON Schema API。
        if hasattr(definition.arguments_model, "model_json_schema"):
            parameters = definition.arguments_model.model_json_schema()
        else:
            parameters = definition.arguments_model.schema()
        definitions.append(
            {
                "type": "function",
                "function": {
                    "name": definition.name,
                    "description": definition.description,
                    "parameters": parameters,
                },
            }
        )
    return definitions


def plan_readonly_tool_with_llm(
    question: str,
    *,
    retrieved_docs: list[Any],
    previous_citations: Optional[list[dict[str, Any]]] = None,
    conversation_context: Optional[dict[str, Any]] = None,
) -> Optional[ToolCallRequest]:
    """让 OpenAI 兼容模型通过原生 tool_calls 选择一个只读工具。

    这里只负责把模型的结构化调用转换成内部协议，真正的权限和参数校验仍由
    execute_readonly_tool() 统一完成。
    """

    from app.services import llm_router_service

    if not llm_router_service.is_llm_router_configured():
        return None

    settings = get_settings()
    candidates = [
        {
            "doc_id": getattr(document, "doc_id", None),
            "chunk_id": getattr(document, "chunk_id", None),
            "knowledge_item_id": getattr(document, "knowledge_item_id", None),
            "title": str(getattr(document, "title", "") or ""),
        }
        for document in retrieved_docs[:10]
    ]
    prompt_lines = [
        f"question: {str(question or '').strip()}",
        "已检索候选或上一轮引用只能作为工具参数候选，不能自行猜测不存在的 ID。",
        "retrieved_candidates: " + json.dumps(candidates, ensure_ascii=False),
        "previous_citations: "
        + json.dumps(previous_citations or [], ensure_ascii=False),
    ]
    raw_context = conversation_context or {}
    recent_messages = list(raw_context.get("recent_messages") or [])
    if recent_messages:
        prompt_lines.append(
            "recent_conversation:\n"
            + "\n".join(
                f"{item.get('role', 'message')}: {item.get('content', '')}"
                for item in recent_messages[-6:]
                if isinstance(item, dict)
            )
        )
    messages = [
        {
            "role": "system",
            "content": (
                "你是企业知识库的只读工具选择器。"
                "只有用户明确要求展开原文、查看切片前后文、读取知识条目、列出文档或查询历史时才调用工具；"
                "普通知识问答不要调用工具。只能使用给定工具，参数必须来自当前候选或上一轮引用。"
            ),
        },
        {"role": "user", "content": "\n".join(prompt_lines)},
    ]
    native_call = llm_router_service.call_openai_compatible_chat_with_tools(
        base_url=settings.llm_router_base_url,
        api_key=settings.llm_router_api_key,
        model=settings.llm_router_model,
        messages=messages,
        tools=build_openai_tool_definitions(),
        timeout_seconds=settings.llm_router_timeout_seconds,
        reasoning_effort=settings.llm_router_reasoning_effort,
    )
    if native_call is None:
        return None
    return ToolCallRequest(
        name=native_call.name,
        arguments=native_call.arguments,
        reason="native tool call",
    )


def execute_readonly_tool(
    request: ToolCallRequest,
    *,
    context: ToolExecutionContext,
    session: Session,
) -> ToolExecutionResult:
    """唯一的工具执行入口，保证未知工具和非法参数不会触发查询。"""

    started_at = time.perf_counter()
    definition = TOOL_DEFINITIONS.get(request.name)
    if definition is None:
        result = ToolExecutionResult(
            tool_name=request.name,
            ok=False,
            error_code="unknown_tool",
            error_message="tool is not registered",
        )
        _finish_tool_call(
            session,
            request=request,
            context=context,
            result=result,
            allowed=False,
            required_permission="",
            reason="unknown tool",
            started_at=started_at,
        )
        return result

    decision = authorize_tool_call(request, context)
    if not decision.allowed:
        result = ToolExecutionResult(
            tool_name=request.name,
            ok=False,
            error_code="forbidden",
            error_message=decision.reason,
        )
        _finish_tool_call(
            session,
            request=request,
            context=context,
            result=result,
            allowed=False,
            required_permission=decision.required_permission,
            reason=decision.reason,
            started_at=started_at,
        )
        return result

    try:
        arguments = definition.arguments_model(**request.arguments)
    except ValidationError as exc:
        result = ToolExecutionResult(
            tool_name=request.name,
            ok=False,
            error_code="invalid_arguments",
            error_message=_validation_message(exc),
        )
        _finish_tool_call(
            session,
            request=request,
            context=context,
            result=result,
            allowed=True,
            required_permission=decision.required_permission,
            reason="invalid arguments",
            started_at=started_at,
        )
        return result

    try:
        data, citations = definition.handler(session, context, arguments)
    except knowledge_tools.KnowledgeToolError as exc:
        result = ToolExecutionResult(
            tool_name=request.name,
            ok=False,
            error_code=exc.code,
            error_message=exc.message,
        )
        _finish_tool_call(
            session,
            request=request,
            context=context,
            result=result,
            allowed=True,
            required_permission=decision.required_permission,
            reason=exc.code,
            started_at=started_at,
        )
        return result
    except Exception:
        # 不把 SQL、路径或第三方 SDK 异常直接暴露给模型。
        result = ToolExecutionResult(
            tool_name=request.name,
            ok=False,
            error_code="execution_error",
            error_message="tool execution failed",
        )
        _finish_tool_call(
            session,
            request=request,
            context=context,
            result=result,
            allowed=True,
            required_permission=decision.required_permission,
            reason="execution error",
            started_at=started_at,
        )
        return result

    result = ToolExecutionResult(
        tool_name=request.name,
        ok=True,
        data=data,
        citations=citations,
    )
    _finish_tool_call(
        session,
        request=request,
        context=context,
        result=result,
        allowed=True,
        required_permission=decision.required_permission,
        reason="success",
        started_at=started_at,
    )
    return result


def _finish_tool_call(
    session: Session,
    *,
    request: ToolCallRequest,
    context: ToolExecutionContext,
    result: ToolExecutionResult,
    allowed: bool,
    required_permission: str,
    reason: str,
    started_at: float,
) -> None:
    duration_seconds = max(0.0, time.perf_counter() - started_at)
    outcome = "success" if result.ok else (result.error_code or "failed")
    get_metrics().record_operation(
        "agent_tool",
        duration_seconds,
        outcome=outcome,
    )
    record_tool_call_audit(
        session,
        request=request,
        context=context,
        result=result,
        allowed=allowed,
        duration_seconds=duration_seconds,
        reason=reason,
        required_permission=required_permission,
    )


def plan_readonly_tool(
    question: str,
    retrieved_docs: list[Any],
    *,
    previous_citations: Optional[list[dict[str, Any]]] = None,
) -> Optional[ToolCallRequest]:
    """第一版确定性工具规划器。

    先用可测试的业务规则决定工具，避免每个问题都额外调用一次 LLM。
    后续可以把这个函数替换成模型 JSON tool call，但仍必须复用 registry 校验。
    """

    normalized = re.sub(r"\s+", "", question or "").lower()
    first_doc = retrieved_docs[0] if retrieved_docs else None

    if any(
        marker in normalized
        for marker in ("出处", "来源", "哪个文件", "哪份文件", "来自哪个", "原文位置", "具体文件", "文件名")
    ):
        document_id = _document_value(first_doc, "doc_id")
        if document_id is None:
            document_id = _citation_value(previous_citations, "doc_id")
        if document_id:
            return ToolCallRequest(
                name="get_document",
                arguments={"document_id": int(document_id)},
                reason="问题要求确认命中原文的具体来源文件",
            )

    if any(marker in normalized for marker in ("上一段", "下一段", "相邻", "前后文", "上下文")):
        chunk_id = _document_value(first_doc, "chunk_id")
        if chunk_id:
            return ToolCallRequest(
                name="get_chunk_neighbors",
                arguments={"chunk_id": int(chunk_id), "radius": 2},
                reason="问题明确要求相邻切片或前后上下文",
            )

    if any(marker in normalized for marker in ("有哪些文档", "文档列表", "所有文档", "文件列表")):
        return ToolCallRequest(
            name="list_knowledge_base_documents",
            arguments={"limit": 20},
            reason="问题要求列出知识库文档",
        )

    if any(marker in normalized for marker in ("完整原文", "全文", "详细内容", "整份文档", "这份文档内容")):
        document_id = _document_value(first_doc, "doc_id")
        if document_id:
            return ToolCallRequest(
                name="get_document",
                arguments={"document_id": int(document_id)},
                reason="问题要求读取完整或详细文档内容",
            )

    if any(marker in normalized for marker in ("知识条目详情", "条目内容", "这个知识条目")):
        item_id = _document_value(first_doc, "knowledge_item_id")
        if item_id:
            return ToolCallRequest(
                name="get_knowledge_item",
                arguments={"knowledge_item_id": int(item_id)},
                reason="问题要求读取知识条目详情",
            )

    return None


def plan_conversation_history_tool(
    question: str,
    *,
    reason: str = "上下文缺口需要查询当前会话历史",
    limit: int = 5,
) -> ToolCallRequest:
    """构造上下文恢复专用工具调用，仍然复用统一 registry。"""

    return ToolCallRequest(
        name="search_conversation_history",
        arguments={"query": str(question or "").strip(), "limit": limit},
        reason=reason,
    )


def _document_value(document: Any, field: str) -> Optional[int]:
    if document is None:
        return None
    value = getattr(document, field, None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _citation_value(citations: Optional[list[dict[str, Any]]], field: str) -> Optional[int]:
    for citation in citations or []:
        if not isinstance(citation, dict):
            continue
        value = citation.get(field)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            continue
    return None


def _validation_message(error: ValidationError) -> str:
    first = error.errors()[0] if error.errors() else {}
    location = ".".join(str(item) for item in first.get("loc", ()))
    message = str(first.get("msg") or "invalid arguments")
    return f"{location}: {message}" if location else message
