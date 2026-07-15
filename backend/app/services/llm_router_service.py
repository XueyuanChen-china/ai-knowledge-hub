import json
import re
from dataclasses import dataclass
from typing import Optional
from urllib import error, request

from app.config import get_settings

DIRECT_ROUTE = "direct"
RAG_ROUTE = "rag"
COMPLEX_ROUTE = "complex"
ALLOWED_ROUTES = {DIRECT_ROUTE, RAG_ROUTE, COMPLEX_ROUTE}


@dataclass
class RouterDecision:
    """LLM Router 的标准输出。"""

    route: str
    reason: str
    raw_output: str = ""


def route_question_with_llm(
    question: str,
    knowledge_base_id: Optional[int],
) -> Optional[RouterDecision]:
    """调用 OpenAI 兼容接口做问题路由。

    如果没有配置 API、调用失败，或者返回内容无法解析，就返回 None，
    让 graph 层继续走规则兜底。
    """

    settings = get_settings()
    if not is_llm_router_configured():
        return None

    messages = build_router_messages(question, knowledge_base_id)

    try:
        raw_output = call_openai_compatible_chat(
            base_url=settings.llm_router_base_url,
            api_key=settings.llm_router_api_key,
            model=settings.llm_router_model,
            messages=messages,
            timeout_seconds=settings.llm_router_timeout_seconds,
        )
    except RuntimeError:
        return None

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
) -> list[dict[str, str]]:
    """构造 Router Prompt。"""

    knowledge_base_text = (
        str(knowledge_base_id) if knowledge_base_id is not None else "null"
    )

    system_prompt = "\n".join(
        [
            "你是企业知识库问答系统的 Router。",
            "你的任务是把用户问题分类成 direct、rag、complex 三种路线之一。",
            "direct: 打招呼、寒暄、通用概念解释、与当前知识库无关的问题。",
            "rag: 需要从知识库里检索一到几段内容即可回答的具体问题。",
            "complex: 需要总结、归纳、对比、梳理整个知识库或多篇文档的复杂问题。",
            "你只能输出 JSON，不要输出额外解释。",
            '格式固定为: {"route":"direct|rag|complex","reason":"一句简短原因"}',
        ]
    )

    user_prompt = "\n".join(
        [
            f"knowledge_base_id: {knowledge_base_text}",
            f"question: {question.strip()}",
        ]
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def call_openai_compatible_chat(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    timeout_seconds: int,
) -> str:
    """调用 OpenAI 兼容 chat/completions 接口。"""

    payload = build_chat_completion_payload(
        model=model,
        messages=messages,
    )

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
        error_body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(
            f"llm router http error: status={exc.code}, body={error_body}"
        ) from exc
    except error.URLError as exc:
        raise RuntimeError(f"llm router network error: {exc.reason}") from exc

    try:
        data = json.loads(response_body)
        return extract_message_content(data)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("llm router response format invalid") from exc


def build_chat_completion_payload(
    *,
    model: str,
    messages: list[dict[str, str]],
) -> dict:
    """构造 OpenAI 兼容 chat/completions 请求体。

    这里显式开启 JSON Mode：
    - response_format={"type":"json_object"}
    - prompt 中必须包含 JSON 关键词
    """

    return {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": 64,
        "response_format": {"type": "json_object"},
    }


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
        match = re.search(r'"route"\s*:\s*"(direct|rag|complex)"', normalized_output, re.I)
        if match:
            route = normalize_route(match.group(1))

    if route is None:
        route = normalize_route(normalized_output)

    if route is None:
        for candidate in (DIRECT_ROUTE, RAG_ROUTE, COMPLEX_ROUTE):
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
