"""FastAPI：后台 Graph Run、实体消歧恢复、SSE 轨迹与状态查询。"""
import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langgraph.types import Command
from pydantic import BaseModel, Field

from graph.builder import build_graph
from models.settings import Settings
from services.checkpoint_service import build_sqlite_checkpointer, close_sqlite_checkpointer
from services.observability import clear_events, emit_event, get_events
from services.resources import close_resources
from services.run_service import RunManager


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    runs.close()
    close_resources()
    close_sqlite_checkpointer()

app = FastAPI(title="科技知识图谱 Multi-Agent GraphRAG", version="0.7.0", lifespan=lifespan)
graph = build_graph(checkpointer=build_sqlite_checkpointer())
runs = RunManager(max_workers=4)
frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=frontend_dir), name="static")


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    thread_id: str | None = Field(default=None, min_length=8, max_length=128)
    max_replans: int = Field(default=2, ge=0, le=10)


class ResumeRequest(BaseModel):
    selections: dict[str, str]


def _config(run_id: str) -> dict:
    return {"configurable": {"thread_id": run_id}}


def _checkpoint_exists(run_id: str) -> bool:
    return bool(graph.get_state(_config(run_id)).values)


def _public_run(run_id: str) -> dict:
    record = runs.get(run_id)
    if record:
        payload = {key: value for key, value in record.items() if key != "result"}
        result = record.get("result") or {}
        if record["status"] == "COMPLETED":
            state = {key: value for key, value in result.items() if key != "__interrupt__"}
            payload.update({"state": state, "final_answer": state.get("final_answer")})
        return payload
    snapshot = graph.get_state(_config(run_id))
    if not snapshot.values:
        raise HTTPException(status_code=404, detail="run_id 不存在")
    status = "NEED_USER_SELECTION" if snapshot.next else "COMPLETED"
    return {"run_id": run_id, "status": status, "state": snapshot.values,
            "final_answer": snapshot.values.get("final_answer"), "next": list(snapshot.next),
            "updated_at": snapshot.created_at}


@app.get("/health")
def health() -> dict:
    settings = Settings.from_env()
    return {"status": "ok", "stage": 7, "model_provider": settings.model_provider,
            "model_name": settings.model_name, "entity_backend": settings.entity_backend,
            "achievement_backend": settings.achievement_backend, "graph_backend": settings.graph_backend,
            "embedding_provider": settings.embedding_provider, "checkpointer": "sqlite", "execution": "background+sse"}


@app.get("/", include_in_schema=False)
def frontend() -> FileResponse:
    return FileResponse(frontend_dir / "index.html")


@app.post("/query", status_code=202)
def query(request: QueryRequest) -> JSONResponse:
    return create_query(request)


@app.post("/queries", status_code=202)
def create_query(request: QueryRequest) -> JSONResponse:
    run_id = request.thread_id or f"run-{uuid4().hex}"
    if runs.exists(run_id) or _checkpoint_exists(run_id):
        raise HTTPException(status_code=409, detail="run_id 已存在，请为新查询使用新的 run_id")
    clear_events(run_id)
    runs.create(run_id)
    emit_event("QUERY_STARTED", thread_id=run_id,
               node_input={"question": request.question, "run_id": run_id, "max_replans": request.max_replans})
    initial = {"thread_id": run_id, "question": request.question, "replan_count": 0,
               "max_replans": request.max_replans, "resolved_entities": {}, "task_history": []}
    runs.submit(run_id, lambda: graph.invoke(initial, config=_config(run_id)))
    return JSONResponse(status_code=202, content={"run_id": run_id, "thread_id": run_id, "status": "RUNNING"})


@app.post("/queries/{run_id}/resume", status_code=202)
def resume_query(run_id: str, request: ResumeRequest) -> JSONResponse:
    record = runs.get(run_id)
    snapshot = graph.get_state(_config(run_id))
    if not record and not snapshot.values:
        raise HTTPException(status_code=404, detail="run_id 不存在")
    if record and record["status"] != "NEED_USER_SELECTION":
        raise HTTPException(status_code=409, detail=f"当前状态 {record['status']} 不允许恢复")
    if not record:
        runs.create(run_id)
    else:
        runs.mark_running(run_id)
    emit_event("QUERY_RESUMED", thread_id=run_id, node_input={"selections": request.selections})
    runs.submit(run_id, lambda: graph.invoke(Command(resume=request.selections), config=_config(run_id)))
    return JSONResponse(status_code=202, content={"run_id": run_id, "thread_id": run_id, "status": "RUNNING"})


@app.get("/queries/{run_id}/events")
def get_query_events(run_id: str, after: int = 0) -> dict:
    events = get_events(run_id, max(0, after))
    cursor = events[-1]["sequence"] if events else max(0, after)
    return {"run_id": run_id, "thread_id": run_id, "events": events, "cursor": cursor}


@app.get("/queries/{run_id}/stream")
async def stream_query_events(run_id: str, after: int = 0) -> StreamingResponse:
    if not runs.exists(run_id) and not _checkpoint_exists(run_id):
        raise HTTPException(status_code=404, detail="run_id 不存在")

    async def generate():
        cursor = max(0, after)
        while True:
            events = get_events(run_id, cursor)
            for event in events:
                cursor = event["sequence"]
                yield f"event: trace\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            record = runs.get(run_id)
            if record and record["status"] in RunManager.TERMINAL:
                payload = _public_run(run_id)
                yield f"event: status\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
                break
            # 进程重启后 RunManager 的内存记录会消失，但 SQLite checkpoint 仍在。
            # 此时直接从 checkpoint 推导终态，避免 SSE 连接永久只发送 heartbeat。
            if not record:
                payload = _public_run(run_id)
                if payload["status"] in RunManager.TERMINAL:
                    yield f"event: status\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
                    break
            yield ": heartbeat\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/queries/{run_id}")
def get_query(run_id: str) -> dict:
    return _public_run(run_id)


@app.get("/queries/{run_id}/history")
def get_query_history(run_id: str, limit: int = 20) -> dict:
    snapshots = list(graph.get_state_history(_config(run_id), limit=limit))
    if not snapshots:
        raise HTTPException(status_code=404, detail="run_id 不存在")
    return {"run_id": run_id, "thread_id": run_id, "history": [
        {"created_at": item.created_at, "next": list(item.next), "state": item.values}
        for item in snapshots
    ]}
