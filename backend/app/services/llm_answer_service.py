import json
from dataclasses import dataclass
from typing import Optional
from urllib import error, request

from app.config import get_settings
from app.services import rag_service


@dataclass
class StructuredAnswerPayload:
    """大模型返回的结构化答案。"""

    answer: str
    used_context_numbers: list[int]
    raw_output: str = ""


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
