import json
from dataclasses import dataclass
from typing import Iterator, Optional
from urllib import error, request

from app.config import get_settings
from app.services import rag_service


@dataclass
class StructuredAnswerPayload:
    """大模型返回的结构化答案。"""

    answer: str
    used_context_numbers: list[int]
    raw_output: str = ""


@dataclass
class StreamAnswerEvent:
    """流式答案事件。"""

    event: str
    text: str = ""
    result: Optional[rag_service.RagAnswerResult] = None


def generate_answer(
    question: str,
    documents: list[rag_service.RetrievedDocument],
) -> rag_service.RagAnswerResult:
    """优先调用 LLM 生成答案，失败时回退到本地抽取式答案。"""

    fallback_result = rag_service.generate_answer(question, documents)
    if not documents:
        return fallback_result

    settings = resolve_answer_settings()
    if not settings.is_configured:
        return fallback_result

    context = rag_service.format_context(documents)
    messages = build_answer_messages(question, context)

    try:
        raw_output = call_openai_compatible_chat(
            base_url=settings.base_url,
            api_key=settings.api_key,
            model=settings.model,
            messages=messages,
            timeout_seconds=settings.timeout_seconds,
        )
        structured = parse_answer_output(raw_output)
    except RuntimeError:
        return fallback_result

    if structured is None or not structured.answer.strip():
        return fallback_result

    citations = build_citations_from_context_numbers(
        documents,
        structured.used_context_numbers,
    )
    if not citations:
        citations = rag_service.build_citations(documents[:1])

    answer = append_reference_labels(structured.answer.strip(), citations, documents)
    return rag_service.RagAnswerResult(
        answer=answer,
        context=context,
        citations=citations,
        used_fallback=False,
    )


def stream_answer(
    question: str,
    documents: list[rag_service.RetrievedDocument],
) -> Iterator[StreamAnswerEvent]:
    """流式生成答案。

    对外统一产出两类事件：
    - delta: 一小段新增文本
    - result: 最终 RagAnswerResult
    """

    fallback_result = rag_service.generate_answer(question, documents)
    if not documents:
        yield from stream_fallback_result(fallback_result)
        return

    settings = resolve_answer_settings()
    if not settings.is_configured:
        yield from stream_fallback_result(fallback_result)
        return

    context = rag_service.format_context(documents)
    messages = build_answer_messages(question, context)
    raw_output_parts: list[str] = []
    streamed_answer = ""

    try:
        for delta in call_openai_compatible_chat_stream(
            base_url=settings.base_url,
            api_key=settings.api_key,
            model=settings.model,
            messages=messages,
            timeout_seconds=settings.timeout_seconds,
        ):
            raw_output_parts.append(delta)
            current_answer = extract_answer_text_from_partial_json(
                "".join(raw_output_parts)
            )
            if not current_answer.startswith(streamed_answer):
                continue

            next_delta = current_answer[len(streamed_answer) :]
            if next_delta:
                streamed_answer = current_answer
                for character in next_delta:
                    yield StreamAnswerEvent(event="delta", text=character)
    except RuntimeError:
        yield from stream_fallback_result(fallback_result)
        return

    raw_output = "".join(raw_output_parts)
    structured = parse_answer_output(raw_output)
    if structured is None or not structured.answer.strip():
        yield from stream_fallback_result(fallback_result)
        return

    citations = build_citations_from_context_numbers(
        documents,
        structured.used_context_numbers,
    )
    if not citations:
        citations = rag_service.build_citations(documents[:1])

    final_answer = append_reference_labels(
        structured.answer.strip(),
        citations,
        documents,
    )
    if final_answer.startswith(streamed_answer):
        for character in final_answer[len(streamed_answer) :]:
            yield StreamAnswerEvent(event="delta", text=character)

    yield StreamAnswerEvent(
        event="result",
        result=rag_service.RagAnswerResult(
            answer=final_answer,
            context=context,
            citations=citations,
            used_fallback=False,
        ),
    )


@dataclass
class AnswerLlmSettings:
    base_url: str
    api_key: str
    model: str
    timeout_seconds: int

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)


def resolve_answer_settings() -> AnswerLlmSettings:
    """解析 Answer Node 的模型配置。

    如果没有单独配置 Answer 参数，就回退到 Router 配置，
    这样第一版只配一套 Qwen 参数也能直接跑通。
    """

    settings = get_settings()
    return AnswerLlmSettings(
        base_url=(
            settings.llm_answer_base_url.strip()
            or settings.llm_router_base_url.strip()
        ),
        api_key=(
            settings.llm_answer_api_key.strip()
            or settings.llm_router_api_key.strip()
        ),
        model=(
            settings.llm_answer_model.strip()
            or settings.llm_router_model.strip()
        ),
        timeout_seconds=settings.llm_answer_timeout_seconds,
    )


def build_answer_messages(question: str, context: str) -> list[dict[str, str]]:
    """构造 Answer Node Prompt。"""

    system_prompt = "\n".join(
        [
            "你是企业知识库问答助手。",
            "请严格基于给定 context 回答，不要使用外部知识补充。",
            "如果 context 不能支持回答，请明确说当前上下文不足。",
            "你必须输出 JSON，不要输出额外解释。",
            '格式固定为: {"answer":"回答正文","used_context_numbers":[1,2]}',
            "used_context_numbers 表示你实际引用了哪些 context 编号。",
        ]
    )

    user_prompt = "\n\n".join(
        [
            f"question: {question.strip()}",
            "context:",
            context.strip(),
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

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 512,
        "response_format": {"type": "json_object"},
    }

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
            f"llm answer http error: status={exc.code}, body={error_body}"
        ) from exc
    except error.URLError as exc:
        raise RuntimeError(f"llm answer network error: {exc.reason}") from exc

    try:
        data = json.loads(response_body)
        return extract_message_content(data)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("llm answer response format invalid") from exc


def call_openai_compatible_chat_stream(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    timeout_seconds: int,
) -> Iterator[str]:
    """调用 OpenAI 兼容流式 chat/completions 接口。"""

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 512,
        "response_format": {"type": "json_object"},
        "stream": True,
    }

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
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="ignore").strip()
                if not line or line.startswith(":") or not line.startswith("data:"):
                    continue

                payload_text = line[len("data:") :].strip()
                if payload_text == "[DONE]":
                    break

                try:
                    payload_data = json.loads(payload_text)
                except json.JSONDecodeError:
                    continue

                delta_text = extract_stream_delta_content(payload_data)
                if delta_text:
                    yield delta_text
    except error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(
            f"llm answer stream http error: status={exc.code}, body={error_body}"
        ) from exc
    except error.URLError as exc:
        raise RuntimeError(f"llm answer stream network error: {exc.reason}") from exc


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


def extract_stream_delta_content(response_data: dict) -> str:
    """从流式 chunk 里取出 delta.content。"""

    delta = response_data["choices"][0]["delta"]
    content = delta.get("content")
    if isinstance(content, list):
        text_parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(str(item.get("text") or ""))
        return "".join(text_parts)
    if content is None:
        return ""
    return str(content)


def parse_answer_output(raw_output: str) -> Optional[StructuredAnswerPayload]:
    """解析模型结构化输出。"""

    normalized_output = strip_markdown_code_fence(raw_output).strip()
    if not normalized_output:
        return None

    try:
        payload = json.loads(normalized_output)
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, dict):
        return None

    answer = str(payload.get("answer") or "").strip()
    used_context_numbers = normalize_context_numbers(payload.get("used_context_numbers"))
    return StructuredAnswerPayload(
        answer=answer,
        used_context_numbers=used_context_numbers,
        raw_output=normalized_output,
    )


def extract_answer_text_from_partial_json(raw_output: str) -> str:
    """从未闭合的 JSON 文本里尽量提取 answer 字段当前已生成的内容。"""

    key_index = raw_output.find('"answer"')
    if key_index < 0:
        return ""

    colon_index = raw_output.find(":", key_index)
    if colon_index < 0:
        return ""

    quote_index = raw_output.find('"', colon_index)
    if quote_index < 0:
        return ""

    index = quote_index + 1
    characters: list[str] = []
    while index < len(raw_output):
        char = raw_output[index]
        if char == '"':
            break
        if char != "\\":
            characters.append(char)
            index += 1
            continue

        index += 1
        if index >= len(raw_output):
            break

        escaped = raw_output[index]
        if escaped in {'"', "\\", "/"}:
            characters.append(escaped)
        elif escaped == "b":
            characters.append("\b")
        elif escaped == "f":
            characters.append("\f")
        elif escaped == "n":
            characters.append("\n")
        elif escaped == "r":
            characters.append("\r")
        elif escaped == "t":
            characters.append("\t")
        elif escaped == "u":
            unicode_slice = raw_output[index + 1 : index + 5]
            if len(unicode_slice) < 4:
                break
            try:
                characters.append(chr(int(unicode_slice, 16)))
            except ValueError:
                break
            index += 4
        else:
            characters.append(escaped)

        index += 1

    return "".join(characters)


def normalize_context_numbers(value: object) -> list[int]:
    """把模型返回的 context 编号统一归一化成 int 列表。"""

    if not isinstance(value, list):
        return []

    normalized_numbers: list[int] = []
    for item in value:
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if number >= 1 and number not in normalized_numbers:
            normalized_numbers.append(number)
    return normalized_numbers


def build_citations_from_context_numbers(
    documents: list[rag_service.RetrievedDocument],
    used_context_numbers: list[int],
) -> list[dict]:
    """把 context 编号映射成真实 citations。"""

    if not used_context_numbers:
        return []

    citations: list[dict] = []
    seen_keys: set[tuple[Optional[int], Optional[int]]] = set()
    for number in used_context_numbers:
        index = number - 1
        if index < 0 or index >= len(documents):
            continue
        document = documents[index]
        citation_key = (document.doc_id, document.chunk_id)
        if citation_key in seen_keys:
            continue
        seen_keys.add(citation_key)
        citations.append(
            {
                "doc_id": document.doc_id,
                "chunk_id": document.chunk_id,
                "knowledge_item_id": document.knowledge_item_id,
                "title": document.title,
                "score": document.score,
            }
        )
    return citations


def append_reference_labels(
    answer: str,
    citations: list[dict],
    documents: list[rag_service.RetrievedDocument],
) -> str:
    """在答案末尾补一个简短引用编号。"""

    if not citations:
        return answer

    labels: list[str] = []
    for citation in citations:
        for index, document in enumerate(documents, start=1):
            if (
                document.chunk_id == citation.get("chunk_id")
                and document.doc_id == citation.get("doc_id")
            ):
                labels.append(f"[{index}]")
                break

    if not labels:
        return answer

    joined_labels = " ".join(labels)
    if "参考来源：" in answer:
        return answer
    return f"{answer}\n\n参考来源：{joined_labels}"


def strip_markdown_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 2:
            return "\n".join(lines[1:-1]).strip()
    return stripped


def stream_fallback_result(
    fallback_result: rag_service.RagAnswerResult,
) -> Iterator[StreamAnswerEvent]:
    """把 fallback 结果也按字符流式吐出来，保证前端体验一致。"""

    for character in fallback_result.answer:
        yield StreamAnswerEvent(event="delta", text=character)
    yield StreamAnswerEvent(event="result", result=fallback_result)
