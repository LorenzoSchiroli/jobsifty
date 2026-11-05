from functools import partial
from typing import Any

from langgraph.graph import StateGraph, END

from backend.search.advanced.state import AdvancedSearchState
from backend.search.advanced.nodes import (
    node_generate_questions, node_clarify, node_search, node_critique, node_fit_score, _route_after_critique,
)


def build_graph(checkpointer, *, biencoder, reranker, os_client) -> Any:
    graph = StateGraph(AdvancedSearchState)
    graph.add_node("generate_questions", node_generate_questions)
    graph.add_node("clarify", node_clarify)
    graph.add_node("search", partial(node_search, biencoder=biencoder, reranker=reranker,
                                     os_client=os_client))
    graph.add_node("critique", node_critique)
    graph.add_node("fit_score", node_fit_score)
    graph.set_entry_point("generate_questions")
    graph.add_edge("generate_questions", "clarify")
    graph.add_edge("clarify", "search")
    graph.add_edge("search", "critique")
    graph.add_conditional_edges("critique", _route_after_critique,
                                {"search": "search", "fit_score": "fit_score"})
    graph.add_edge("fit_score", END)
    return graph.compile(checkpointer=checkpointer)
