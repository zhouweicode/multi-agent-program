"""支持查询、消歧恢复、状态和历史查询的持久化 FastAPI 层。"""
from typing import Any
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from langgraph.types import Command
from graph.builder import build_graph
from models.settings import Settings
from services.checkpoint_service import build_sqlite_checkpointer
from services.observability import emit_event

app = FastAPI(title="科技知识图谱 Multi-Agent GraphRAG", version="0.6.0")
graph = build_graph(checkpointer=build_sqlite_checkpointer())


class QueryRequest(BaseModel):
    question: str
    thread_id: str
    max_replans: int = Field(default=2, ge=0, le=10)


class ResumeRequest(BaseModel):
    selections: dict[str, str]


def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def _response(thread_id: str, result: dict[str, Any]) -> dict:
    interrupts = result.get("__interrupt__", ())
    if interrupts:
        return {"thread_id": thread_id, "status": "NEED_USER_SELECTION", "interrupt": interrupts[0].value}
    return {"thread_id": thread_id, "status": "COMPLETED", "state": result,
            "final_answer": result.get("final_answer")}


@app.get("/health")
def health() -> dict:
    settings = Settings.from_env()
    return {"status": "ok", "stage": 6, "model_provider": settings.model_provider,
            "entity_backend": settings.entity_backend, "achievement_backend": settings.achievement_backend,
            "graph_backend": settings.graph_backend, "embedding_provider": settings.embedding_provider,
            "checkpointer": "sqlite"}


@app.post("/query")
def query(request: QueryRequest) -> dict:
    return create_query(request)


@app.post("/queries")
def create_query(request: QueryRequest) -> dict:
    emit_event("QUERY_STARTED", thread_id=request.thread_id)
    initial = {"thread_id": request.thread_id, "question": request.question, "replan_count": 0, "max_replans": request.max_replans,
               "resolved_entities": {}, "task_history": []}
    result = graph.invoke(initial, config=_config(request.thread_id))
    return _response(request.thread_id, result)


@app.post("/queries/{thread_id}/resume")
def resume_query(thread_id: str, request: ResumeRequest) -> dict:
    snapshot = graph.get_state(_config(thread_id))
    if not snapshot.values:
        raise HTTPException(status_code=404, detail="thread_id 不存在")
    emit_event("QUERY_RESUMED", thread_id=thread_id)
    result = graph.invoke(Command(resume=request.selections), config=_config(thread_id))
    return _response(thread_id, result)


@app.get("/queries/{thread_id}")
def get_query(thread_id: str) -> dict:
    snapshot = graph.get_state(_config(thread_id))
    if not snapshot.values:
        raise HTTPException(status_code=404, detail="thread_id 不存在")
    return {"thread_id": thread_id, "state": snapshot.values, "next": list(snapshot.next),
            "created_at": snapshot.created_at}


@app.get("/queries/{thread_id}/history")
def get_query_history(thread_id: str, limit: int = 20) -> dict:
    snapshots = list(graph.get_state_history(_config(thread_id), limit=limit))
    if not snapshots:
        raise HTTPException(status_code=404, detail="thread_id 不存在")
    return {"thread_id": thread_id, "history": [
        {"created_at": item.created_at, "next": list(item.next), "state": item.values}
        for item in snapshots
    ]}
