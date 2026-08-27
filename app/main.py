"""FastAPI：后台 Graph Run、实体消歧恢复、SSE 轨迹与状态查询。"""
import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langgraph.types import Command
from pydantic import BaseModel, Field

from graph.builder import build_graph
from mcp_runtime.client import mcp_server_health
from models.settings import Settings
from repositories.run_repository import SQLiteRunRepository
from services.checkpoint_service import (
    build_sqlite_checkpointer,
    close_sqlite_checkpointer,
)
from services.conversation_memory import close_conversation_memory, conversation_memory_repository
from services.observability import clear_events, emit_event, get_events
from services.telemetry import (
    activate_trace,
    finish_run_trace,
    prepare_run_trace,
    repository as observability_repository,
    run_metadata,
    traced_span,
)
from services.resources import (
    active_release_settings,
    close_resources,
    get_achievement_service,
    get_enterprise_service,
    get_entity_service,
    get_graph_service,
    get_industry_service,
)
from services.run_service import RunManager


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    runs.close()
    close_conversation_memory()
    close_resources()
    close_sqlite_checkpointer()

app = FastAPI(title="科技知识图谱 Multi-Agent GraphRAG", version="0.9.0", lifespan=lifespan)
graph = build_graph(checkpointer=build_sqlite_checkpointer())
settings = Settings.from_env()
runs = RunManager(max_workers=settings.run_max_workers, timeout_seconds=settings.run_timeout_seconds,
                  repository=SQLiteRunRepository(settings.run_registry_path))
frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=frontend_dir), name="static")


@app.middleware("http")
async def prevent_stale_frontend_assets(request: Request, call_next):
    """开发演示页始终校验资源版本，避免浏览器继续执行旧版前端。"""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    thread_id: str | None = Field(default=None, min_length=8, max_length=128)
    max_replans: int = Field(default=2, ge=0, le=10)
    web_search_enabled: bool = True
    conversation_id: str | None = Field(default=None, min_length=8, max_length=128)
    memory_enabled: bool = False


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
        if record["status"] == "COMPLETED" and result:
            state = {key: value for key, value in result.items() if key != "__interrupt__"}
            payload.update({"state": state, "final_answer": state.get("final_answer")})
        elif record.get("persisted"):
            snapshot = graph.get_state(_config(run_id))
            payload.update({"state": snapshot.values, "final_answer": snapshot.values.get("final_answer")})
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
    _, active_release = active_release_settings(settings)
    return {"status": "ok", "stage": 9, "model_provider": settings.model_provider,
            "model_name": settings.model_name, "entity_backend": settings.entity_backend,
            "achievement_backend": settings.achievement_backend, "graph_backend": settings.graph_backend,
            "enterprise_backend": settings.enterprise_backend, "industry_backend": settings.industry_backend,
            "embedding_provider": settings.embedding_provider, "tool_transport": settings.tool_transport,
            "mcp_server_url": settings.mcp_server_url if settings.tool_transport == "mcp" else None,
            "checkpointer": "sqlite", "execution": "background+sse",
            "active_kg_release": active_release.get("release_id") if active_release else None,
            "active_milvus_collection": active_release.get("milvus_collection") if active_release else settings.milvus_collection}


@app.get("/health/dependencies")
def dependency_health() -> JSONResponse:
    """主动探测启用的数据后端；异常被隔离且不暴露密码。"""
    checks = {}
    settings = Settings.from_env()
    factories = (("entity", get_entity_service),) if settings.tool_transport == "mcp" else (
        ("entity", get_entity_service), ("achievement", get_achievement_service),
        ("enterprise", get_enterprise_service), ("industry", get_industry_service), ("graph", get_graph_service))
    for name, factory in factories:
        try:
            checks[name] = factory().health()
        except Exception as exc:  # noqa: BLE001 - 健康探针必须隔离任意第三方客户端异常
            checks[name] = {"ready": False, "error_type": type(exc).__name__, "message": str(exc)}
    if settings.tool_transport == "mcp":
        checks["mcp"] = mcp_server_health(settings.mcp_server_url, settings.mcp_request_timeout)
    ready = all(item.get("ready", False) for item in checks.values())
    return JSONResponse(status_code=200 if ready else 503,
                        content={"status": "ok" if ready else "degraded", "stage": 9, "dependencies": checks})


@app.get("/metrics")
def metrics() -> dict:
    """教学版运行指标；不暴露问题、State 或密钥。"""
    return runs.stats()


@app.get("/", include_in_schema=False)
def frontend() -> FileResponse:
    return FileResponse(frontend_dir / "index.html")


@app.post("/query", status_code=202)
def query(request: QueryRequest) -> JSONResponse:
    return create_query(request)


@app.post("/queries", status_code=202)
def create_query(request: QueryRequest) -> JSONResponse:
    run_id = request.thread_id or f"run-{uuid4().hex}"
    conversation_id = (request.conversation_id or f"conv-{uuid4().hex}") if request.memory_enabled else None
    if runs.exists(run_id) or _checkpoint_exists(run_id):
        raise HTTPException(status_code=409, detail="run_id 已存在，请为新查询使用新的 run_id")
    trace_context = prepare_run_trace(run_id, run_metadata())
    submitted = False
    try:
        with activate_trace(trace_context):
            with traced_span("api.queries.create", "api", {"http.route": "/queries", "run.id": run_id}):
                clear_events(run_id)
                runs.create(run_id)
                emit_event("QUERY_STARTED", thread_id=run_id,
                           node_input={"question": request.question, "run_id": run_id, "max_replans": request.max_replans,
                                       "web_search_enabled": request.web_search_enabled,
                                       "memory_enabled": request.memory_enabled,
                                       "conversation_id": conversation_id})
                initial = {"thread_id": run_id, "question": request.question, "replan_count": 0,
                           "max_replans": request.max_replans, "web_search_enabled": request.web_search_enabled,
                           "conversation_id": conversation_id, "memory_enabled": request.memory_enabled,
                           "resolved_entities": {}, "task_history": []}
                runs.submit(run_id, lambda: graph.invoke(initial, config=_config(run_id)), trace_context=trace_context)
                submitted = True
    except Exception as exc:
        runs.fail(run_id, exc)
        if not submitted:
            finish_run_trace(trace_context, "FAILED")
        raise
    return JSONResponse(status_code=202, content={"run_id": run_id, "thread_id": run_id,
                                                   "conversation_id": conversation_id,
                                                   "memory_enabled": request.memory_enabled,
                                                   "trace_id": trace_context.trace_id, "status": "RUNNING"})


def _validate_conversation_id(conversation_id: str) -> None:
    if not 8 <= len(conversation_id) <= 128:
        raise HTTPException(status_code=422, detail="conversation_id 长度必须在 8 到 128 之间")


@app.get("/conversations/{conversation_id}/memory")
def get_conversation_memory(conversation_id: str) -> dict:
    _validate_conversation_id(conversation_id)
    memory = conversation_memory_repository().get(conversation_id)
    # API 只返回上下文元数据；历史最终答案保留在存储中但不在此接口批量暴露。
    return {key: value for key, value in memory.items() if key != "turns"} | {
        "recent_turns": memory.get("turns", [])
    }


@app.delete("/conversations/{conversation_id}/memory")
def clear_conversation_memory(conversation_id: str) -> dict:
    _validate_conversation_id(conversation_id)
    result = conversation_memory_repository().clear(conversation_id)
    emit_event("MEMORY_CLEARED", conversation_id=conversation_id,
               deleted_turns=result["deleted_turns"], deleted_entities=result["deleted_entities"])
    return result


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
    trace_context = prepare_run_trace(run_id, run_metadata())
    submitted = False
    try:
        with activate_trace(trace_context):
            with traced_span("api.queries.resume", "api", {"http.route": "/queries/{run_id}/resume", "run.id": run_id}):
                emit_event("QUERY_RESUMED", thread_id=run_id, node_input={"selections": request.selections})
                runs.submit(run_id, lambda: graph.invoke(Command(resume=request.selections), config=_config(run_id)),
                            trace_context=trace_context)
                submitted = True
    except Exception as exc:
        runs.fail(run_id, exc)
        if not submitted:
            finish_run_trace(trace_context, "FAILED")
        raise
    return JSONResponse(status_code=202, content={"run_id": run_id, "thread_id": run_id,
                                                   "trace_id": trace_context.trace_id, "status": "RUNNING"})


@app.get("/observability/summary")
def observability_summary(limit: int = 200) -> dict:
    return observability_repository().summary(max(1, min(limit, 500)))


@app.get("/observability/runs")
def observability_runs(limit: int = 50) -> dict:
    rows = observability_repository().list_runs(max(1, min(limit, 500)))
    return {"runs": rows, "count": len(rows)}


@app.get("/observability/runs/{run_id}")
def observability_run(run_id: str) -> dict:
    row = observability_repository().get_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="Trace Run 不存在")
    return row


@app.get("/observability/compare")
def observability_compare(left_run_id: str, right_run_id: str) -> dict:
    left = observability_repository().get_run(left_run_id)
    right = observability_repository().get_run(right_run_id)
    if not left or not right:
        raise HTTPException(status_code=404, detail="待比较的 Run 不存在")
    metric_names = ("duration_ms", "input_tokens", "output_tokens", "total_tokens", "cost",
                    "tool_calls", "tool_successes", "error_count", "replan_count", "attempt_count")
    deltas = {name: round(float(right["summary"][name]) - float(left["summary"][name]), 8)
              for name in metric_names}
    left_names = [span["name"] for span in left["spans"]]
    right_names = [span["name"] for span in right["spans"]]
    return {"left": left, "right": right, "delta": deltas,
            "span_diff": {"only_left": sorted(set(left_names) - set(right_names)),
                          "only_right": sorted(set(right_names) - set(left_names))}}


@app.post("/queries/{run_id}/cancel", status_code=202)
def cancel_query(run_id: str) -> JSONResponse:
    if not runs.get(run_id):
        raise HTTPException(status_code=404, detail="run_id 不存在")
    if not runs.cancel(run_id):
        raise HTTPException(status_code=409, detail="当前 Run 已结束或不可取消")
    return JSONResponse(status_code=202, content={"run_id": run_id, "status": runs.get(run_id)["status"]})


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
