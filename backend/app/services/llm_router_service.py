import json
import re
import time
from dataclasses import dataclass
from typing import Any, Optional
from urllib import error, request

from app.config import get_settings
from app.observability.metrics import get_metrics
from app.services import context_manager

DIRECT_ROUTE = "direct"
RAG_ROUTE = "rag"
TOOL_ROUTE = "tool"
ALLOWED_ROUTES = {DIRECT_ROUTE, RAG_ROUTE, TOOL_ROUTE}


@dataclass
class RouterDecision:
    """LLM Router 的标准输出。"""

    route: str
    reason: str
    raw_output: str = ""


@dataclass
class NativeToolCall:
    """OpenAI 兼容接口返回的原生工具调用。"""

    name: str
    arguments: dict[str, Any]
    call_id: str = ""
    raw_output: str = ""


def route_question_with_llm(
    question: str,
    knowledge_base_id: Optional[int],
    conversation_context: Optional[dict] = None,
) -> Optional[RouterDecision]:
    """调用 OpenAI 兼容接口做问题路由。

    如果没有配置 API、调用失败，或者返回内容无法解析，就返回 None，
    让 graph 层继续走规则兜底。
    """

    settings = get_settings()
    if not is_llm_router_configured():
        return None

    messages = build_router_messages(
        question,
        knowledge_base_id,
        conversation_context=conversation_context,
    )

    started_at = time.perf_counter()
    try:
        raw_output = call_openai_compatible_chat(
            base_url=settings.llm_router_base_url,
            api_key=settings.llm_router_api_key,
            model=settings.llm_router_model,
            messages=messages,
            timeout_seconds=settings.llm_router_timeout_seconds,
            reasoning_effort=settings.llm_router_reasoning_effort,
        )
    except RuntimeError:
        get_metrics().record_operation(
            "llm_router", time.perf_counter() - started_at, outcome="error"
        )
        return None

    get_metrics().record_operation(
        "llm_router", time.perf_counter() - started_at, outcome="success"
    )

    return parse_router_output(raw_output)


def is_llm_router_configured() -> bool:
    """判断当前是否已经配置了可用的 Router 参数。"""

    settings = get_settings()
    return bool(
        settings.llm_router_base_url.strip()
        and settings.llm_router_api_key.strip()
        and settings.llm_router_model.strip()
    )


def build_router_messages(
    question: str,
    knowledge_base_id: Optional[int],
    conversation_context: Optional[dict] = None,
) -> list[dict[str, str]]:
    """构造 Router Prompt。"""

    knowledge_base_text = (
        str(knowledge_base_id) if knowledge_base_id is not None else "null"
    )

    system_prompt = "\n".join(
        [
            "你是企业知识库问答系统的 Router。",
            "你的任务是把用户问题分类成 direct、rag、tool 三种路线之一。",
            "direct: 打招呼、寒暄、通用概念解释、与当前知识库无关的问题。",
            "rag: 需要从知识库里检索内容的问题，包括事实查询、流程问题、跨文档总结、归纳和对比。",
            "tool: 用户引用上一轮已经找到的文档或切片，要求展开原文、查看前后文或列出文档；此路线不要重新做向量检索。",
            "你只能输出 JSON，不要输出额外解释。",
            '格式固定为: {"route":"direct|rag|tool","reason":"一句简短原因"}',
        ]
    )

    prompt_lines = [
        f"knowledge_base_id: {knowledge_base_text}",
        f"question: {question.strip()}",
    ]
    raw_context = conversation_context or {}
    context_pack = context_manager.build_context_pack(
        purpose="router",
        messages=list(raw_context.get("recent_messages") or []),
        summary=raw_context.get("conversation_summary")
        or str(raw_context.get("summary") or ""),
        current_question=question,
        system_instructions=list(raw_context.get("system_instructions") or []),
        persistent_memory=list(raw_context.get("persistent_memory") or []),
        relevant_history=list(raw_context.get("relevant_history") or []),
    )
    if context_pack.system_instructions:
        system_prompt += "\n本次请求的附加约束：\n" + "\n".join(
            context_pack.system_instructions
        )
    if context_pack.summary:
        prompt_lines.append(f"conversation summary:\n{context_pack.summary}")
    if context_pack.recent_messages:
        prompt_lines.append(
            "recent conversation:\n"
            + "\n".join(
                f"{item['role']}: {item['content']}"
                for item in context_pack.recent_messages
            )
        )
    if context_pack.persistent_memory:
        prompt_lines.append(
            "persistent memory:\n"
            + "\n".join(item.content for item in context_pack.persistent_memory)
        )
    if context_pack.relevant_history:
        prompt_lines.append(
            "relevant conversation history:\n"
            + "\n".join(item.content for item in context_pack.relevant_history)
        )
    previous_citations = raw_context.get("previous_citations") or []
    if previous_citations:
        prompt_lines.append(
            "previous retrieval citations:\n"
            + json.dumps(previous_citations[:10], ensure_ascii=False)
        )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "\n".join(prompt_lines)},
    ]


def call_openai_compatible_chat(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    timeout_seconds: int,
    max_tokens: int = 64,
    json_mode: bool = True,
    reasoning_effort: str = "",
) -> str:
    """调用 OpenAI 兼容 chat/completions 接口。"""

    payload = build_chat_completion_payload(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        json_mode=json_mode,
        reasoning_effort=reasoning_effort,
    )

    return extract_message_content(
        post_chat_completion(
            base_url=base_url,
            api_key=api_key,
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
    )


def call_openai_compatible_chat_with_tools(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    timeout_seconds: int,
    max_tokens: int = 256,
    reasoning_effort: str = "",
) -> Optional[NativeToolCall]:
    """使用 OpenAI 兼容的 tools/tool_choice 请求原生工具调用。"""

    payload = build_chat_completion_payload(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        json_mode=False,
        reasoning_effort=reasoning_effort,
    )
    payload["tools"] = tools
    payload["tool_choice"] = "auto"
    response_data = post_chat_completion(
        base_url=base_url,
        api_key=api_key,
        payload=payload,
        timeout_seconds=timeout_seconds,
    )
    return parse_native_tool_call(response_data)


def post_chat_completion(
    *,
    base_url: str,
    api_key: str,
    payload: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    """发送一次 Chat Completions 请求并返回完整 JSON。"""

    endpoint = base_url.rstrip("/") + "/chat/completions"
    body = json.dumps(payload).encode("utf-8")
    http_request = request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )

    try:
        with request.urlopen(http_request, timeout=timeout_seconds) as response:
            response_body = response.read().decode("utf-8")
    except error.HTTPError as exc:
        raise RuntimeError(f"llm router http error: status={exc.code}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"llm router network error: {exc.reason}") from exc

    try:
        data = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("llm router response format invalid") from exc
    if not isinstance(data, dict):
        raise RuntimeError("llm router response format invalid")
    return data


def parse_native_tool_call(response_data: dict[str, Any]) -> Optional[NativeToolCall]:
    """从 Chat Completions 响应中解析第一个原生 tool_call。"""

    try:
        message = response_data["choices"][0]["message"]
        tool_calls = message.get("tool_calls") or []
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("llm tool call response format invalid") from exc

    if not tool_calls:
        return None
    first = tool_calls[0]
    function = first.get("function") if isinstance(first, dict) else None
    if not isinstance(function, dict):
        raise RuntimeError("llm tool call function format invalid")
    name = str(function.get("name") or "").strip()
    raw_arguments = function.get("arguments")
    if not name or raw_arguments is None:
        raise RuntimeError("llm tool call is missing name or arguments")
    if isinstance(raw_arguments, str):
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            raise RuntimeError("llm tool call arguments are not valid JSON") from exc
    else:
        arguments = raw_arguments
    if not isinstance(arguments, dict):
        raise RuntimeError("llm tool call arguments must be an object")
    return NativeToolCall(
        name=name,
        arguments=arguments,
        call_id=str(first.get("id") or ""),
        raw_output=json.dumps(first, ensure_ascii=False),
    )


def build_chat_completion_payload(
    *,
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int = 64,
    json_mode: bool = True,
    reasoning_effort: str = "",
) -> dict:
    """构造 OpenAI 兼容 chat/completions 请求体。

    这里显式开启 JSON Mode：
    - response_format={"type":"json_object"}
    - prompt 中必须包含 JSON 关键词
    """

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if reasoning_effort.strip():
        payload["reasoning_effort"] = reasoning_effort.strip()
    return payload


def extract_message_content(response_data: dict) -> str:
    """从 OpenAI 兼容返回体里取出 message.content。"""

    content = response_data["choices"][0]["message"]["content"]
    if isinstance(content, list):
        text_parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(str(item.get("text") or ""))
        return "".join(text_parts).strip()
    return str(content).strip()


def parse_router_output(raw_output: str) -> Optional[RouterDecision]:
    """把模型输出解析成统一 Route。"""

    normalized_output = strip_markdown_code_fence(raw_output).strip()
    if not normalized_output:
        return None

    route: Optional[str] = None
    reason = ""

    try:
        payload = json.loads(normalized_output)
    except json.JSONDecodeError:
        payload = None

    if isinstance(payload, dict):
        route = normalize_route(payload.get("route"))
        reason = str(payload.get("reason") or "").strip()

    if route is None:
        match = re.search(r'"route"\s*:\s*"(direct|rag|tool|complex)"', normalized_output, re.I)
        if match:
            route = normalize_route(match.group(1))

    if route is None:
        route = normalize_route(normalized_output)

    if route is None:
        for candidate in (DIRECT_ROUTE, RAG_ROUTE, TOOL_ROUTE):
            if re.search(rf"\b{candidate}\b", normalized_output, re.I):
                route = candidate
                break

    if route is None:
        return None

    if not reason:
        reason = "llm router"

    return RouterDecision(
        route=route,
        reason=reason,
        raw_output=normalized_output,
    )


def normalize_route(value: object) -> Optional[str]:
    """把各种大小写或带空格的 route 归一化。"""

    normalized = str(value or "").strip().lower()
    # 兼容旧 Router 或旧 checkpoint 中的 complex，但不再暴露占位分支。
    # 复杂问题暂时统一进入已有 RAG 链路。
    if normalized == "complex":
        return RAG_ROUTE
    if normalized in ALLOWED_ROUTES:
        return normalized
    return None


def strip_markdown_code_fence(text: str) -> str:
    """去掉模型常见的 ```json ... ``` 包裹。"""

    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 2:
            return "\n".join(lines[1:-1]).strip()
    return stripped
