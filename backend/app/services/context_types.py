"""Context Management 的结构化数据类型。

这些类型只描述一次 LLM 请求需要携带的上下文，不改变 messages 表中的完整历史。
所有类型都提供字典序列化，方便进入 LangGraph checkpoint 和 SSE 响应。
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class ContextItem:
    """可以作为一个整体被选入或移出 ContextPack 的内容单元。"""

    kind: str
    content: str
    source_ids: List[str] = field(default_factory=list)
    importance: float = 0.0
    pinned: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StructuredSummary:
    """可追踪的会话摘要，而不是一段无法定位来源的纯文本。"""

    version: int = 2
    facts: List[str] = field(default_factory=list)
    decisions: List[str] = field(default_factory=list)
    open_questions: List[str] = field(default_factory=list)
    entities: List[str] = field(default_factory=list)
    source_message_ids: List[int] = field(default_factory=list)
    summarized_through_message_id: Optional[int] = None
    generated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_prompt_text(self) -> str:
        """生成给模型看的摘要文本，避免模型依赖内部字段名。"""

        sections = []
        for label, values in (
            ("已知事实", self.facts),
            ("已确认决定", self.decisions),
            ("未解决问题", self.open_questions),
            ("关键实体", self.entities),
        ):
            if values:
                sections.append(label + "：" + "；".join(values))
        return "\n".join(sections)

    @classmethod
    def from_dict(cls, value: Any) -> Optional["StructuredSummary"]:
        if not isinstance(value, dict):
            return None
        try:
            return cls(
                version=int(value.get("version") or 2),
                facts=_string_list(value.get("facts")),
                decisions=_string_list(value.get("decisions")),
                open_questions=_string_list(value.get("open_questions")),
                entities=_string_list(value.get("entities")),
                source_message_ids=_int_list(value.get("source_message_ids")),
                summarized_through_message_id=_optional_int(
                    value.get("summarized_through_message_id")
                ),
                generated_at=str(value.get("generated_at") or ""),
            )
        except (TypeError, ValueError):
            return None

    @classmethod
    def from_legacy_text(cls, value: str) -> Optional["StructuredSummary"]:
        text = str(value or "").strip()
        if not text:
            return None
        return cls(facts=[text])


@dataclass
class EvidenceItem:
    """检索证据单元，默认整体保留，避免截断半个 chunk。"""

    content: str
    source_id: str = ""
    document_id: Optional[int] = None
    chunk_id: Optional[int] = None
    knowledge_item_id: Optional[int] = None
    title: str = ""
    score: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ToolResultRef:
    """工具结果的受控引用和摘要。

    完整结果保存在持久化记录中；Context Pack 只携带 summary、source_ids
    和 result_ref。这样工具输出可以从当前上下文淘汰，但仍能按引用恢复。
    """

    tool_name: str
    summary: str
    source_ids: List[str] = field(default_factory=list)
    result_ref: str = ""
    content: str = ""
    truncated: bool = False
    error: str = ""
    created_at: str = ""
    used_in_answer: bool = False
    citation_used: bool = False
    importance: str = "normal"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BudgetBreakdown:
    """ContextPack 的预算使用情况，便于日志和评测。"""

    total: int
    system_instructions: int = 0
    summary: int = 0
    recent_messages: int = 0
    persistent_memory: int = 0
    relevant_history: int = 0
    evidence: int = 0
    tools: int = 0
    used: int = 0
    remaining: int = 0

    def to_dict(self) -> Dict[str, int]:
        return asdict(self)


@dataclass
class OmittedItem:
    kind: str
    source_id: str
    reason: str
    estimated_tokens: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RecoveryAction:
    action: str
    reason: str
    success: bool
    source_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _int_list(value: Any) -> List[int]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result


def _optional_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    return int(value)
