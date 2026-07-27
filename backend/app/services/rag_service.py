import re
from dataclasses import dataclass
from typing import Optional

from sqlmodel import Session, select

from app.db.models import KnowledgeBase, KnowledgeItem
from app.services.vector_service import SemanticSearchHit, search_similar_chunks


@dataclass
class RetrievedDocument:
    """RAG 检索阶段统一使用的文档结果。"""

    doc_id: Optional[int]
    chunk_id: Optional[int]
    knowledge_item_id: Optional[int]
    title: str
    content: str
    score: float
    metadata: dict


@dataclass
class RagAnswerResult:
    """RAG 生成阶段的返回结果。"""

    answer: str
    context: str
    citations: list[dict]
    used_fallback: bool


def retrieve(
    question: str,
    knowledge_base_id: int,
    session: Session,
    *,
    organization_id: Optional[int] = None,
    top_k: int = 5,
) -> list[RetrievedDocument]:
    """根据问题从知识库里检索相关 chunk。"""

    normalized_question = question.strip()
    if not normalized_question:
        raise ValueError("question must not be empty")

    statement = select(KnowledgeBase).where(KnowledgeBase.id == knowledge_base_id)
    if organization_id is not None:
        statement = statement.where(KnowledgeBase.organization_id == organization_id)
    knowledge_base = session.exec(statement).first()
    if knowledge_base is None:
        raise ValueError("knowledge base not found")
    resolved_organization_id = knowledge_base.organization_id

    hits = search_similar_chunks(
        organization_id=resolved_organization_id,
        knowledge_base_id=knowledge_base_id,
        query=normalized_question,
        top_k=top_k,
    )
    title_map = build_knowledge_item_title_map(
        hits,
        organization_id=resolved_organization_id,
        session=session,
    )

    documents: list[RetrievedDocument] = []
    for hit in hits:
        title = title_map.get(hit.knowledge_item_id or 0)
        if not title:
            title = str(hit.metadata.get("filename") or f"Knowledge Item {hit.knowledge_item_id}")

        documents.append(
            RetrievedDocument(
                doc_id=hit.document_id,
                chunk_id=hit.chunk_id,
                knowledge_item_id=hit.knowledge_item_id,
                title=title,
                content=hit.content,
                score=hit.score,
                metadata=hit.metadata,
            )
        )

    return documents


def format_context(documents: list[RetrievedDocument]) -> str:
    """把检索结果拼成统一上下文字符串。"""

    if not documents:
        return ""

    context_parts: list[str] = []
    for index, document in enumerate(documents, start=1):
        context_parts.append(
            "\n".join(
                [
                    f"[{index}] 标题：{document.title}",
                    f"doc_id: {document.doc_id}",
                    f"chunk_id: {document.chunk_id}",
                    f"score: {document.score:.4f}",
                    "内容：",
                    document.content.strip(),
                ]
            )
        )

    return "\n\n".join(context_parts)


def generate_answer(
    question: str,
    documents: list[RetrievedDocument],
) -> RagAnswerResult:
    """根据检索结果生成第一版答案。

    当前先实现可稳定运行的抽取式答案生成。
    后面接入真正的大模型时，可以保持函数签名不变，只替换内部实现。
    """

    normalized_question = question.strip()
    if not normalized_question:
        raise ValueError("question must not be empty")

    context = format_context(documents)
    if not documents:
        return RagAnswerResult(
            answer="当前知识库里没有检索到与问题直接相关的内容。",
            context=context,
            citations=[],
            used_fallback=True,
        )

    answer_body = build_extractive_answer(normalized_question, documents)
    citations = build_citations(documents)
    answer = f"根据当前知识库检索结果，{answer_body}"

    return RagAnswerResult(
        answer=answer,
        context=context,
        citations=citations,
        used_fallback=True,
    )


def build_knowledge_item_title_map(
    hits: list[SemanticSearchHit],
    *,
    organization_id: int,
    session: Session,
) -> dict[int, str]:
    """批量查询知识条目标题。"""

    knowledge_item_ids = {
        hit.knowledge_item_id for hit in hits if hit.knowledge_item_id is not None
    }
    if not knowledge_item_ids:
        return {}

    statement = select(KnowledgeItem).where(
        KnowledgeItem.id.in_(knowledge_item_ids),
        KnowledgeItem.organization_id == organization_id,
    )
    knowledge_items = session.exec(statement).all()
    return {item.id: item.title for item in knowledge_items if item.id is not None}


def build_extractive_answer(
    question: str,
    documents: list[RetrievedDocument],
    *,
    max_sentences: int = 3,
) -> str:
    """用检索结果拼一个可读的抽取式答案。"""

    selected_sentences: list[str] = []
    for document in documents:
        ranked_sentences = rank_document_sentences(question, document.content)
        for sentence in ranked_sentences:
            if sentence in selected_sentences:
                continue
            selected_sentences.append(sentence)
            if len(selected_sentences) >= max_sentences:
                break
        if len(selected_sentences) >= max_sentences:
            break

    if not selected_sentences:
        selected_sentences = [documents[0].content.strip()]

    joined = " ".join(selected_sentences).strip()
    if not joined.endswith(("。", ".", "！", "!", "？", "?")):
        joined += "。"
    return joined


def rank_document_sentences(question: str, content: str) -> list[str]:
    """按和问题的相关性给句子做一个轻量排序。"""

    sentences = split_sentences(content)
    if not sentences:
        return []

    question_terms = extract_query_terms(question)
    scored_sentences = []

    for index, sentence in enumerate(sentences):
        score = 0
        for term in question_terms:
            if term and term in sentence:
                score += len(term)

        # 让文档靠前的句子在同分时略占优，便于保持可读性。
        score += max(0, 5 - index) * 0.01
        scored_sentences.append((score, index, sentence))

    scored_sentences.sort(key=lambda item: (-item[0], item[1]))
    return [sentence for _, _, sentence in scored_sentences]


def split_sentences(content: str) -> list[str]:
    """按中英文常见句号切句。"""

    normalized = re.sub(r"\s+", " ", content).strip()
    if not normalized:
        return []

    raw_sentences = re.split(r"(?<=[。！？!?；;])\s*", normalized)
    return [sentence.strip() for sentence in raw_sentences if sentence.strip()]


def extract_query_terms(question: str) -> list[str]:
    """抽取一个尽量简单但足够稳定的关键词集合。"""

    normalized = re.sub(r"\s+", "", question)
    if not normalized:
        return []

    ascii_terms = [
        token.lower()
        for token in re.findall(r"[A-Za-z0-9_]+", question)
        if len(token) >= 2
    ]

    cjk_terms = set()
    cjk_sequences = re.findall(r"[\u4e00-\u9fff]{2,}", normalized)
    for sequence in cjk_sequences:
        cjk_terms.add(sequence)
        if len(sequence) > 4:
            for size in (4, 3, 2):
                for index in range(0, len(sequence) - size + 1):
                    cjk_terms.add(sequence[index : index + size])

    return list(cjk_terms) + ascii_terms


def build_citations(documents: list[RetrievedDocument]) -> list[dict]:
    """把检索结果转换成引用信息。"""

    citations = []
    for document in documents:
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
