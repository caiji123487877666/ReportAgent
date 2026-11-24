"""组装多智能体研报工作流。"""

from typing import Literal

from langgraph.graph import END, START, StateGraph

from .nodes import (
    analyst_node,
    export_node,
    reviewer_node,
    searcher_node,
    writer_node,
)
from .schemas import ReportTeamState


def route_after_review(state: ReportTeamState) -> Literal["writer", "exporter"]:
    """审核通过或达到两次修订后导出，否则返回写作节点。"""

    if state.is_approved or state.revision_count >= 2:
        return "exporter"
    return "writer"


def build_graph():
    """创建并编译研报团队状态图。"""

    workflow = StateGraph(ReportTeamState)
    workflow.add_node("searcher", searcher_node)
    workflow.add_node("analyst", analyst_node)
    workflow.add_node("writer", writer_node)
    workflow.add_node("reviewer", reviewer_node)
    workflow.add_node("exporter", export_node)

    workflow.add_edge(START, "searcher")
    workflow.add_edge("searcher", "analyst")
    workflow.add_edge("analyst", "writer")
    workflow.add_edge("writer", "reviewer")
    workflow.add_conditional_edges(
        "reviewer",
        route_after_review,
        {"writer": "writer", "exporter": "exporter"},
    )
    workflow.add_edge("exporter", END)
    return workflow.compile()


graph = build_graph()
