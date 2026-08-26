"""上下文预算和轻量 Token 估算。

第一版不绑定某个模型 tokenizer，使用偏保守的字符估算，先保证不同节点
拥有明确、可测试的预算边界。后续替换估算器时不需要改 Context Pack 接口。
"""

from dataclasses import dataclass
from math import ceil

from app.config import get_settings


def estimate_tokens(text: str) -> int:
    """粗略估算文本 Token 数。

    中文通常比英文消耗更多 Token，因此按约 2 个字符估算 1 Token，
    这是预算保护而不是计费精确值。
    """

    normalized = str(text or "")
    if not normalized:
        return 0
    return max(1, ceil(len(normalized) / 2))


def _effective_input_budget(
    configured_budget: int,
    model_window_tokens: int,
    *,
    output_reserve_tokens: int,
    prompt_overhead_tokens: int,
    reasoning_safety_margin_tokens: int,
) -> int:
    """把模型总窗口换算成当前请求可使用的输入预算。

    model_window_tokens 为 0 时保持现有应用级预算，避免未知模型窗口导致
    本地开发行为突然变化。
    """

    if model_window_tokens <= 0:
        return max(1, int(configured_budget))
    available = (
        int(model_window_tokens)
        - max(0, int(output_reserve_tokens))
        - max(0, int(prompt_overhead_tokens))
        - max(0, int(reasoning_safety_margin_tokens))
    )
    return max(1, min(int(configured_budget), available))


@dataclass(frozen=True)
class ContextBudget:
    """一次 Context Pack 的预算。"""

    max_tokens: int
    max_recent_rounds: int
    max_recent_messages: int
    max_message_chars: int
    max_summary_tokens: int
    max_retrieval_tokens: int
    max_tool_tokens: int
    max_history_tokens: int
    max_persistent_memory_tokens: int
    max_system_tokens: int

    @classmethod
    def for_purpose(cls, purpose: str) -> "ContextBudget":
        settings = get_settings()
        normalized = purpose.strip().lower()
        if normalized == "router":
            return cls(
                max_tokens=_effective_input_budget(
                    settings.context_router_max_tokens,
                    settings.context_router_model_window_tokens,
                    output_reserve_tokens=settings.context_output_reserve_tokens,
                    prompt_overhead_tokens=settings.context_prompt_overhead_tokens,
                    reasoning_safety_margin_tokens=settings.context_reasoning_safety_margin_tokens,
                ),
                max_recent_rounds=1,
                max_recent_messages=2,
                max_message_chars=settings.context_message_max_chars,
                max_summary_tokens=settings.context_router_summary_max_tokens,
                max_retrieval_tokens=0,
                max_tool_tokens=0,
                max_history_tokens=settings.context_router_history_max_tokens,
                max_persistent_memory_tokens=settings.context_router_memory_max_tokens,
                max_system_tokens=settings.context_system_max_tokens,
            )
        if normalized == "rewrite":
            return cls(
                max_tokens=_effective_input_budget(
                    settings.context_rewrite_max_tokens,
                    settings.context_rewrite_model_window_tokens,
                    output_reserve_tokens=settings.context_output_reserve_tokens,
                    prompt_overhead_tokens=settings.context_prompt_overhead_tokens,
                    reasoning_safety_margin_tokens=settings.context_reasoning_safety_margin_tokens,
                ),
                max_recent_rounds=4,
                max_recent_messages=settings.context_rewrite_recent_messages,
                max_message_chars=settings.context_message_max_chars,
                max_summary_tokens=settings.context_rewrite_summary_max_tokens,
                max_retrieval_tokens=0,
                max_tool_tokens=0,
                max_history_tokens=settings.context_rewrite_history_max_tokens,
                max_persistent_memory_tokens=settings.context_rewrite_memory_max_tokens,
                max_system_tokens=settings.context_system_max_tokens,
            )
        if normalized == "answer":
            return cls(
                max_tokens=_effective_input_budget(
                    settings.context_answer_max_tokens,
                    settings.context_answer_model_window_tokens,
                    output_reserve_tokens=settings.context_output_reserve_tokens,
                    prompt_overhead_tokens=settings.context_prompt_overhead_tokens,
                    reasoning_safety_margin_tokens=settings.context_reasoning_safety_margin_tokens,
                ),
                max_recent_rounds=settings.context_answer_recent_rounds,
                max_recent_messages=settings.context_answer_recent_messages,
                max_message_chars=settings.context_message_max_chars,
                max_summary_tokens=settings.context_answer_summary_max_tokens,
                max_retrieval_tokens=settings.context_answer_retrieval_max_tokens,
                max_tool_tokens=settings.context_answer_tool_max_tokens,
                max_history_tokens=settings.context_answer_history_max_tokens,
                max_persistent_memory_tokens=settings.context_answer_memory_max_tokens,
                max_system_tokens=settings.context_system_max_tokens,
            )
        raise ValueError(f"unsupported context purpose: {purpose}")
