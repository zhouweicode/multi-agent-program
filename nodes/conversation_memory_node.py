"""LangGraph nodes for conversation memory recall and writeback."""
from graph.state import GraphRAGState
from services.conversation_memory import recall_conversation_memory, write_conversation_memory


def conversation_memory_recall_node(state: GraphRAGState) -> dict:
    return recall_conversation_memory(state)


def conversation_memory_writeback_node(state: GraphRAGState) -> dict:
    return write_conversation_memory(state)
