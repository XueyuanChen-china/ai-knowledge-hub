"""固定检索样本的离线评测工具。"""

import json
from dataclasses import asdict, dataclass, field
from math import log2
from pathlib import Path
from typing import Callable, Iterable, Optional

from app.services.vector_service import SemanticSearchHit


@dataclass(frozen=True)
class EvaluationCase:
    """一条可重复执行的检索评测样本。"""

    case_id: str
    query: str
    expected_chunk_ids: set[int]
    category: str
    notes: str = ""
    # 外部公开基准使用稳定的 corpus document id，而不是本地数据库自增 chunk id。
    expected_external_ids: set[str] = field(default_factory=set)
    relevance_by_external_id: dict[str, float] = field(default_factory=dict)
    dataset: str = ""
    source_query_id: str = ""

@dataclass
class EvaluationCaseResult:
    case_id: str
    category: str
    expected_chunk_ids: list[int]
    retrieved_chunk_ids: list[int]
    first_relevant_rank: Optional[int]
    passed: bool
    retrieved_evidence_ids: list[str]


@dataclass
class RetrievalEvaluationReport:
    """U8 基线与升级结果可共同使用的稳定指标结构。"""

    top_k: int
    case_count: int
    answerable_case_count: int
    no_answer_case_count: int
    recall_at_k: float
    mrr: float
    citation_correctness: float
    no_answer_rejection_rate: float
    case_results: list[EvaluationCaseResult]
    ndcg_at_k: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def load_evaluation_cases(path: Path) -> list[EvaluationCase]:
    """从 JSON fixture 读取评测样本，避免评测问题散落在测试代码里。"""

    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_cases = payload.get("cases", [])
    cases: list[EvaluationCase] = []
    for raw_case in raw_cases:
        cases.append(
            EvaluationCase(
                case_id=str(raw_case["case_id"]),
                query=str(raw_case["query"]),
                expected_chunk_ids={int(item) for item in raw_case.get("expected_chunk_ids", [])},
                category=str(raw_case["category"]),
                notes=str(raw_case.get("notes", "")),
                expected_external_ids={
                    str(item) for item in raw_case.get("expected_external_ids", [])
                },
                relevance_by_external_id={
                    str(item): float(score)
                    for item, score in raw_case.get("relevance_by_external_id", {}).items()
                },
                dataset=str(raw_case.get("dataset", "")),
                source_query_id=str(raw_case.get("source_query_id", "")),
            )
        )
    return cases


def _hit_evidence_id(hit: SemanticSearchHit) -> str:
    """返回评测用稳定证据 ID。

    外部基准导入 ES 时会把 corpus id 写入 metadata；旧的本地 fixture 则继续
    使用 chunk_id。这样评测不会把本地数据库自增主键误当成公开数据集的真值。
    """

    for key in ("benchmark_doc_id", "external_doc_id"):
        value = hit.metadata.get(key)
        if value not in (None, ""):
            return str(value)
    if hit.chunk_id is not None:
        return str(hit.chunk_id)
    return hit.vector_id


def _hit_is_relevant(hit: SemanticSearchHit, case: EvaluationCase) -> bool:
    """判断一条命中是否属于当前样本的相关证据。"""

    if case.expected_external_ids:
        return _hit_evidence_id(hit) in case.expected_external_ids
    return hit.chunk_id in case.expected_chunk_ids


def _hit_relevance(hit: SemanticSearchHit, case: EvaluationCase) -> float:
    """读取 qrels 等级；旧 fixture 使用二值相关性。"""

    if case.relevance_by_external_id:
        return case.relevance_by_external_id.get(_hit_evidence_id(hit), 0.0)
    return 1.0 if _hit_is_relevant(hit, case) else 0.0


def _ndcg(relevances: list[float], ideal_relevances: list[float], top_k: int) -> float:
    """计算单条 query 的 nDCG@K。"""

    def dcg(values: list[float]) -> float:
        return sum((2**value - 1) / log2(index + 2) for index, value in enumerate(values[:top_k]))

    ideal = dcg(sorted(ideal_relevances, reverse=True))
    return dcg(relevances) / ideal if ideal else 0.0


def evaluate_retrieval_cases(
    cases: Iterable[EvaluationCase],
    retrieve: Callable[[EvaluationCase], list[SemanticSearchHit]],
    *,
    top_k: int = 5,
) -> RetrievalEvaluationReport:
    """计算 Recall@K、MRR、引用正确率和无答案拒答率。"""

    if top_k < 1:
        raise ValueError("top_k must be at least 1")

    case_results: list[EvaluationCaseResult] = []
    reciprocal_ranks: list[float] = []
    answerable_hits = 0
    citation_correct_hits = 0
    citation_total = 0
    no_answer_rejections = 0
    answerable_case_count = 0
    no_answer_case_count = 0
    ndcg_values: list[float] = []

    for case in cases:
        hits = retrieve(case)[:top_k]
        retrieved_chunk_ids = [hit.chunk_id for hit in hits if hit.chunk_id is not None]
        retrieved_evidence_ids = [_hit_evidence_id(hit) for hit in hits]
        expected_chunk_ids = case.expected_chunk_ids
        has_external_truth = bool(case.expected_external_ids)
        is_no_answer = (
            case.category == "no_answer"
            or (not expected_chunk_ids and not has_external_truth)
        )

        if is_no_answer:
            no_answer_case_count += 1
            passed = not retrieved_chunk_ids
            no_answer_rejections += int(passed)
            case_results.append(
                EvaluationCaseResult(
                    case_id=case.case_id,
                    category=case.category,
                    expected_chunk_ids=[],
                    retrieved_chunk_ids=retrieved_chunk_ids,
                    first_relevant_rank=None,
                    passed=passed,
                    retrieved_evidence_ids=retrieved_evidence_ids,
                )
            )
            continue

        answerable_case_count += 1
        first_relevant_rank = next(
            (
                index
                for index, hit in enumerate(hits, start=1)
                if _hit_is_relevant(hit, case)
            ),
            None,
        )
        passed = first_relevant_rank is not None
        answerable_hits += int(passed)
        reciprocal_ranks.append(1.0 / first_relevant_rank if first_relevant_rank else 0.0)

        citation_total += len(hits)
        citation_correct_hits += sum(
            1 for hit in hits if _hit_is_relevant(hit, case)
        )
        ndcg_values.append(
            _ndcg(
                [_hit_relevance(hit, case) for hit in hits],
                list(case.relevance_by_external_id.values())
                if case.relevance_by_external_id
                else [1.0 for _ in expected_chunk_ids],
                top_k,
            )
        )
        case_results.append(
            EvaluationCaseResult(
                case_id=case.case_id,
                category=case.category,
                expected_chunk_ids=sorted(expected_chunk_ids),
                retrieved_chunk_ids=retrieved_chunk_ids,
                first_relevant_rank=first_relevant_rank,
                passed=passed,
                retrieved_evidence_ids=retrieved_evidence_ids,
            )
        )

    return RetrievalEvaluationReport(
        top_k=top_k,
        case_count=len(case_results),
        answerable_case_count=answerable_case_count,
        no_answer_case_count=no_answer_case_count,
        recall_at_k=(answerable_hits / answerable_case_count if answerable_case_count else 0.0),
        mrr=(sum(reciprocal_ranks) / answerable_case_count if answerable_case_count else 0.0),
        citation_correctness=(citation_correct_hits / citation_total if citation_total else 0.0),
        no_answer_rejection_rate=(
            no_answer_rejections / no_answer_case_count if no_answer_case_count else 0.0
        ),
        case_results=case_results,
        ndcg_at_k=(sum(ndcg_values) / len(ndcg_values) if ndcg_values else 0.0),
    )


def save_evaluation_report(report: RetrievalEvaluationReport, path: Path) -> None:
    """保存 JSON 报告，方便把 dense baseline 与 hybrid 结果并排比较。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
