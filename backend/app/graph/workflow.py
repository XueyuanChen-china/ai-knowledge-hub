from dataclasses import dataclass

from sqlmodel import Session

from app.graph.nodes import (
    DIRECT_ROUTE,
    RAG_ROUTE,
    TOOL_ROUTE,
    answer_node,
    context_gap_check_node,
    direct_answer_node,
    history_recovery_node,
    query_rewrite_node,
    relevance_check_node,
    retrieve_node,
    review_required_node,
    router_node,
    tool_call_node,
    tool_decision_node,
)
from app.graph.state import GraphState


@dataclass
class BasicGraphWorkflow:
    """Day 15 基础图。

    当前只实现最小分支：

    START -> router -> direct
                    -> rag -> retrieve -> relevance_check -> answer/review
                    -> tool -> tool_call -> answer/review

    这一版先不引入更重的图框架依赖，把状态和节点边界先固定住。
    后面如果切到 LangGraph，也可以直接复用：
    - GraphState
    - router_node
    - retrieve_node
    - relevance_check_node
    - answer_node
    - direct_answer_node
    """

    retrieve_top_k: int = 5

    def invoke(
        self,
        state: GraphState,
        session: Session,
    ) -> GraphState:
        routed_state = router_node(state)
        route = routed_state.get("route")

        if route == DIRECT_ROUTE:
            return direct_answer_node(routed_state)

        if route == RAG_ROUTE:
            gap_state = context_gap_check_node(routed_state)
            recovered_state = history_recovery_node(gap_state, session)
            rewritten_state = query_rewrite_node(recovered_state)
            retrieved_state = retrieve_node(
                rewritten_state,
                session,
                top_k=self.retrieve_top_k,
            )
            planned_state = tool_decision_node(retrieved_state)
            tool_state = tool_call_node(planned_state, session)
            checked_state = relevance_check_node(tool_state)
            if checked_state.get("need_human_review"):
                return review_required_node(checked_state)
            return answer_node(checked_state)

        if route == TOOL_ROUTE:
            planned_state = tool_decision_node(routed_state)
            tool_state = tool_call_node(planned_state, session)
            if tool_state.get("tool_error"):
                return review_required_node(tool_state)
            return answer_node(tool_state)

        raise ValueError(f"unsupported route: {route}")


def build_basic_workflow(*, retrieve_top_k: int = 5) -> BasicGraphWorkflow:
    """构造基础图实例。"""

    return BasicGraphWorkflow(retrieve_top_k=retrieve_top_k)
