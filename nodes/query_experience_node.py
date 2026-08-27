"""LangGraph nodes for query experience recall and terminal distillation."""
from graph.state import GraphRAGState
from services.query_experience import recall_query_experience, write_query_experience


def query_experience_recall_node(state: GraphRAGState) -> dict:
    return recall_query_experience(state)


def query_experience_writeback_node(state: GraphRAGState) -> dict:
    return write_query_experience(state)
