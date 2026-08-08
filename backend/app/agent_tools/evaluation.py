"""只读工具 planner 的离线确定性评估。"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union

from app.agent_tools.registry import plan_readonly_tool


@dataclass(frozen=True)
class ToolEvaluationCase:
    question: str
    expected_tool: Optional[str]
    candidate_document_id: Optional[int] = 1
    candidate_chunk_id: Optional[int] = 2
    candidate_knowledge_item_id: Optional[int] = 3


def load_tool_evaluation_cases(path: Union[str, Path]) -> list[ToolEvaluationCase]:
    """从版本化 JSON fixture 读取工具选择评估样本。"""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("tool evaluation fixture must be a JSON array")

    cases: list[ToolEvaluationCase] = []
    for item in payload:
        if not isinstance(item, dict) or not isinstance(item.get("question"), str):
            raise ValueError("each tool evaluation case needs a question")
        expected_tool = item.get("expected_tool")
        if expected_tool is not None and not isinstance(expected_tool, str):
            raise ValueError("expected_tool must be a string or null")
        cases.append(
            ToolEvaluationCase(
                question=item["question"],
                expected_tool=expected_tool,
                candidate_document_id=item.get("candidate_document_id", 1),
                candidate_chunk_id=item.get("candidate_chunk_id", 2),
                candidate_knowledge_item_id=item.get("candidate_knowledge_item_id", 3),
            )
        )
    return cases


def evaluate_tool_planner(cases: list[ToolEvaluationCase]) -> dict[str, Any]:
    correct = 0
    rows: list[dict[str, Any]] = []
    for case in cases:
        candidate = _candidate(
            case.candidate_document_id,
            case.candidate_chunk_id,
            case.candidate_knowledge_item_id,
        )
        request = plan_readonly_tool(case.question, candidate)
        actual_tool = request.name if request is not None else None
        is_correct = actual_tool == case.expected_tool
        if is_correct:
            correct += 1
        rows.append(
            {
                "question": case.question,
                "expected_tool": case.expected_tool,
                "actual_tool": actual_tool,
                "correct": is_correct,
            }
        )

    total = len(cases)
    return {
        "case_count": total,
        "correct_count": correct,
        "tool_selection_accuracy": correct / total if total else 0.0,
        "cases": rows,
    }


def _candidate(document_id: Optional[int], chunk_id: Optional[int], item_id: Optional[int]) -> list[Any]:
    from app.services.rag_service import RetrievedDocument

    return [
        RetrievedDocument(
            doc_id=document_id,
            chunk_id=chunk_id,
            knowledge_item_id=item_id,
            title="evaluation",
            content="evaluation content",
            score=0.9,
            metadata={},
        )
    ]
