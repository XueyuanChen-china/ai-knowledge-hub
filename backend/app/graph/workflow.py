from dataclasses import dataclass

from sqlmodel import Session

from app.graph.nodes import (
    COMPLEX_ROUTE,
    DIRECT_ROUTE,
    RAG_ROUTE,
    answer_node,
    complex_answer_node,
    direct_answer_node,
    relevance_check_node,
    retrieve_node,
    review_required_node,
    router_node,
)
from app.graph.state import GraphState


@dataclass
class BasicGraphWorkflow:
    """Day 15 基础图。

    当前只实现最小分支：

    START -> router -> direct
                    -> rag -> retrieve -> relevance_check -> answer/review
                    -> complex

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
            retrieved_state = retrieve_node(
                routed_state,
                session,
                top_k=self.retrieve_top_k,
            )
            checked_state = relevance_check_node(retrieved_state)
            if checked_state.get("need_human_review"):
                return review_required_node(checked_state)
            return answer_node(checked_state)

        if route == COMPLEX_ROUTE:
            return complex_answer_node(routed_state)

        raise ValueError(f"unsupported route: {route}")


def build_basic_workflow(*, retrieve_top_k: int = 5) -> BasicGraphWorkflow:
    """构造基础图实例。"""

    return BasicGraphWorkflow(retrieve_top_k=retrieve_top_k)
