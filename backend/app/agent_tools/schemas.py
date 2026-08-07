"""只读 Agent 工具的参数和结果协议。

这些模型是工具边界的一部分。即使调用来源是模型，也必须先经过参数校验，
不能把未校验的字典直接拼进 SQL 查询。
"""

import json
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SearchKnowledgeBaseArgs(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=5, ge=1, le=10)

    class Config:
        extra = "forbid"


class GetDocumentArgs(BaseModel):
    document_id: int = Field(gt=0)

    class Config:
        extra = "forbid"


class GetKnowledgeItemArgs(BaseModel):
    knowledge_item_id: int = Field(gt=0)

    class Config:
        extra = "forbid"


class GetChunkNeighborsArgs(BaseModel):
    chunk_id: int = Field(gt=0)
    radius: int = Field(default=2, ge=1, le=3)

    class Config:
        extra = "forbid"


class ListKnowledgeBaseDocumentsArgs(BaseModel):
    limit: int = Field(default=20, ge=1, le=50)

    class Config:
        extra = "forbid"


class SearchConversationHistoryArgs(BaseModel):
    """只搜索当前会话的历史，不允许模型传入 conversation_id 越权扩展范围。"""

    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=10)

    class Config:
        extra = "forbid"


class ToolCallRequest(BaseModel):
    """内部统一的结构化工具调用。"""

    name: str = Field(min_length=1, max_length=100)
    arguments: Dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=500)

    class Config:
        extra = "forbid"


class ToolExecutionContext(BaseModel):
    """工具执行时由后端注入的可信边界。"""

    organization_id: int = Field(gt=0)
    knowledge_base_id: int = Field(gt=0)
    user_id: Optional[int] = Field(default=None, gt=0)
    role: str = Field(default="viewer", min_length=1, max_length=50)
    conversation_id: Optional[int] = Field(default=None, gt=0)
    request_id: str = Field(default="", max_length=100)
    trace_id: str = Field(default="", max_length=100)
    required_permission: str = Field(default="", max_length=100)

    class Config:
        extra = "forbid"


class ToolExecutionResult(BaseModel):
    """工具成功或失败都使用同一个可序列化结果协议。"""

    tool_name: str
    ok: bool
    data: Dict[str, Any] = Field(default_factory=dict)
    citations: List[Dict[str, Any]] = Field(default_factory=list)
    error_code: Optional[str] = None
    error_message: Optional[str] = None

    def to_context_text(self) -> str:
        """生成进入 Context Manager 的受控文本，而不是把 Python 对象直接交给模型。"""

        payload: Dict[str, Any] = {
            "tool": self.tool_name,
            "ok": self.ok,
            "data": self.data,
        }
        if self.citations:
            payload["citations"] = self.citations
        if self.error_code:
            payload["error_code"] = self.error_code
        if self.error_message:
            payload["error_message"] = self.error_message
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
