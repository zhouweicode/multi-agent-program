"""FastAPI：后台 Graph Run、实体消歧恢复、SSE 轨迹与状态查询。"""
import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langgraph.types import Command
from pydantic import BaseModel, Field

from agents.runtime_resources import close_shared_runtime_resources
from graph.builder import build_graph
from mcp_runtime.client import mcp_server_health
from models.settings import Settings
from repositories.run_repository import SQLiteRunRepository
from services.auth import SESSION_COOKIE_NAME, auth_service, close_auth_services
from services.checkpoint_service import (
    build_sqlite_checkpointer,
    close_sqlite_checkpointer,
)
from services.memory_admin import (
    MEMORY_CATEGORIES,
    create_manual_fact,
    delete_manual_fact,
    export_user_memory,
    list_user_facts,
    memory_summary,
    update_manual_fact,
)
from services.memory_errors import MemoryRevisionConflict
from services.memory_manager import close_memory_manager, memory_manager
from services.memory_update_worker import (
    start_memory_update_worker,
    stop_memory_update_worker,
)
from services.observability import clear_events, emit_event, get_events
from services.query_experience import query_experience_stats
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
from services.telemetry import (
    activate_trace,
    finish_run_trace,
    prepare_run_trace,
    run_metadata,
    traced_span,
)
from services.telemetry import (
    repository as observability_repository,
)
from skills.registry import SkillGateError, skill_registry


@asynccontextmanager
async def lifespan(_app: FastAPI):
    start_memory_update_worker()
    yield
    runs.close()
    stop_memory_update_worker()
    close_memory_manager()
    close_auth_services()
    close_shared_runtime_resources()
    close_resources()
    close_sqlite_checkpointer()

app = FastAPI(title="科技知识图谱 Multi-Agent GraphRAG", version="1.0.0", lifespan=lifespan)
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


@app.middleware("http")
async def authenticate_request(request: Request, call_next):
    """Resolve a persisted login session and protect application APIs."""
    path = request.url.path
    token = request.cookies.get(SESSION_COOKIE_NAME)
    user = auth_service().authenticate(token) if token else None
    request.state.user = user

    public_path = (
        path == "/"
        or path == "/health"
        or path.startswith("/static/")
        or path.startswith("/auth/")
    )
    current_settings = Settings.from_env()
    if not public_path and user is None:
        if current_settings.auth_required:
            response = JSONResponse(
                status_code=401,
                content={"detail": "请先登录后再访问系统"},
            )
            if token:
                response.delete_cookie(SESSION_COOKIE_NAME, path="/")
            return response
        request.state.user = {
            "user_id": "local-user",
            "username": "local",
            "display_name": "本地测试用户",
            "active": True,
        }
    return await call_next(request)


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    thread_id: str | None = Field(default=None, min_length=8, max_length=128)
    max_replans: int = Field(default=2, ge=0, le=10)
    web_search_enabled: bool = True
    conversation_id: str | None = Field(default=None, min_length=8, max_length=128)
    memory_enabled: bool = False
    experience_memory_enabled: bool = True
    requested_skill: Literal["expert_report", "industry_landscape"] | None = None
    skill_input: dict[str, Any] = Field(default_factory=dict)


class ResumeRequest(BaseModel):
    selections: dict[str, str]


class LoginRequest(BaseModel):
    user_id: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class SkillToggleRequest(BaseModel):
    enabled: bool


MemoryCategory = Literal[
    "preference", "focus", "correction", "constraint", "output_format", "context"
]


class MemoryFactCreateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=500)
    category: MemoryCategory = "context"
    confidence: float = Field(default=1.0, ge=0, le=1)
    expected_valid_until: datetime | None = None


class MemoryFactUpdateRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    content: str | None = Field(default=None, min_length=1, max_length=500)
    category: MemoryCategory | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    expected_valid_until: datetime | None = None
    status: Literal["active", "archived"] | None = None


class ClearAllMemoryRequest(BaseModel):
    confirmation: Literal["DELETE_ALL_PERSONAL_MEMORY"]


class MemoryFactReviewRequest(BaseModel):
    action: Literal["renew", "archive"]
    expected_revision: int = Field(ge=1)
    review_days: int = Field(default=90, ge=1, le=3650)


def _config(run_id: str) -> dict:
    return {"configurable": {"thread_id": run_id}}


def _checkpoint_exists(run_id: str) -> bool:
    return bool(graph.get_state(_config(run_id)).values)


def _authenticated_user(request: Request) -> dict[str, Any]:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录后再访问系统")
    return user


@app.get("/auth/users")
def list_login_users() -> dict:
    """Return only public account choices used by the login dropdown."""
    users = auth_service().list_users()
    return {"users": users, "count": len(users)}


@app.post("/auth/login")
def login(payload: LoginRequest) -> JSONResponse:
    authenticated = auth_service().login(payload.user_id, payload.password)
    if authenticated is None:
        raise HTTPException(status_code=401, detail="用户或密码不正确")
    token, user = authenticated
    current_settings = Settings.from_env()
    response = JSONResponse(content={"user": user})
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=current_settings.auth_session_ttl_seconds,
        httponly=True,
        secure=current_settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )
    return response


@app.get("/auth/me")
def current_user(request: Request) -> dict:
    return {"user": _authenticated_user(request)}


@app.post("/auth/logout")
def logout(request: Request) -> JSONResponse:
    auth_service().logout(request.cookies.get(SESSION_COOKIE_NAME))
    response = JSONResponse(content={"status": "logged_out"})
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return response


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
    domains = (
        "talent",
        "achievement",
        "enterprise",
        "industry",
        "graph",
        "verification",
        "web",
    )
    transports = {domain: settings.tool_transport_for(domain) for domain in domains}
    active_mcp_domains = {
        domain for domain, transport in transports.items() if transport == "mcp"
    }
    servers = [
        {
            "name": server.name,
            "enabled": server.enabled,
            "domains": list(server.domains),
            "tool_prefix": server.tool_prefix,
            "allowed_tool_count": len(server.allowed_tools),
        }
        for server in settings.resolved_mcp_servers()
        if server.enabled
        and active_mcp_domains
        and (not server.domains or active_mcp_domains.intersection(server.domains))
    ]
    return {
        "status": "ok",
        "stage": 9,
        "model_provider": settings.model_provider,
        "model_name": settings.model_name,
        "entity_backend": settings.entity_backend,
        "achievement_backend": settings.achievement_backend,
        "graph_backend": settings.graph_backend,
        "enterprise_backend": settings.enterprise_backend,
        "industry_backend": settings.industry_backend,
        "embedding_provider": settings.embedding_provider,
        "tool_transport": settings.tool_transport,
        "tool_transports": transports,
        "memory_backend": settings.memory_backend,
        "memory_extraction_enabled": settings.memory_extraction_enabled,
        "memory_retrieval_backend": settings.memory_retrieval_backend,
        "memory_milvus_collection": settings.memory_milvus_collection,
        "mcp_servers": servers,
        "checkpointer": "sqlite",
        "execution": "background+sse",
        "active_kg_release": active_release.get("release_id")
        if active_release
        else None,
        "active_milvus_collection": active_release.get("milvus_collection")
        if active_release
        else settings.milvus_collection,
    }


@app.get("/health/dependencies")
def dependency_health() -> JSONResponse:
    """主动探测启用的数据后端；异常被隔离且不暴露密码。"""
    checks = {}
    settings = Settings.from_env()
    try:
        manager = memory_manager()
        checks["memory"] = manager.health() | {
            "update_jobs": manager.update_job_stats(),
        }
    except Exception as exc:  # noqa: BLE001 - 健康探针隔离外部数据库异常
        checks["memory"] = {
            "ready": False,
            "error_type": type(exc).__name__,
            "message": str(exc),
        }
    factories = [("entity", get_entity_service)]
    domain_factories = (
        ("achievement", get_achievement_service),
        ("enterprise", get_enterprise_service),
        ("industry", get_industry_service),
        ("graph", get_graph_service),
    )
    factories.extend(
        (domain, factory)
        for domain, factory in domain_factories
        if settings.tool_transport_for(domain) == "local"
    )
    for name, factory in factories:
        try:
            checks[name] = factory().health()
        except Exception as exc:  # noqa: BLE001 - 健康探针必须隔离任意第三方客户端异常
            checks[name] = {
                "ready": False,
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
    mcp_domains = {
        domain
        for domain in (
            "talent",
            "achievement",
            "enterprise",
            "industry",
            "graph",
            "verification",
            "web",
        )
        if settings.tool_transport_for(domain) == "mcp"
    }
    for server in settings.resolved_mcp_servers():
        if not server.enabled:
            continue
        if mcp_domains and (
            not server.domains or mcp_domains.intersection(server.domains)
        ):
            checks[f"mcp:{server.name}"] = mcp_server_health(
                server.target, settings.mcp_request_timeout
            )
    ready = all(item.get("ready", False) for item in checks.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ok" if ready else "degraded",
            "stage": 9,
            "dependencies": checks,
        },
    )


@app.get("/metrics")
def metrics() -> dict:
    """教学版运行指标；不暴露问题、State 或密钥。"""
    return runs.stats()


@app.get("/skills")
def list_runtime_skills() -> dict:
    """公开可调用的运行时 Skill 元数据，不暴露 Agent/Tool 权限。"""
    return {"skills": skill_registry.list()}


@app.patch("/skills/{skill_id}")
def toggle_runtime_skill(
    skill_id: str, payload: SkillToggleRequest, request: Request
) -> dict:
    """第一版仅允许内置管理员启停仓库内可信 Skill。"""
    user = _authenticated_user(request)
    if user.get("username") != "admin":
        raise HTTPException(status_code=403, detail="只有系统管理员可以启停 Skill")
    try:
        spec = skill_registry.set_enabled(skill_id, payload.enabled)
    except SkillGateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "skill_id": spec.skill_id,
        "enabled": spec.enabled,
        "version": spec.version,
        "content_hash": spec.content_hash,
    }


@app.get("/", include_in_schema=False)
def frontend() -> FileResponse:
    return FileResponse(frontend_dir / "index.html")


@app.post("/query", status_code=202)
def query(payload: QueryRequest, http_request: Request) -> JSONResponse:
    return create_query(payload, http_request)


@app.post("/queries", status_code=202)
def create_query(payload: QueryRequest, http_request: Request) -> JSONResponse:
    user = _authenticated_user(http_request)
    user_id = str(user["user_id"])
    run_id = payload.thread_id or f"run-{uuid4().hex}"
    conversation_id = (payload.conversation_id or f"conv-{uuid4().hex}") if payload.memory_enabled else None
    if runs.exists(run_id) or _checkpoint_exists(run_id):
        raise HTTPException(status_code=409, detail="run_id 已存在，请为新查询使用新的 run_id")
    trace_context = prepare_run_trace(run_id, run_metadata())
    submitted = False
    try:
        with activate_trace(trace_context):
            with traced_span("api.queries.create", "api", {"http.route": "/queries", "run.id": run_id}):
                clear_events(run_id)
                runs.create(run_id)
                if conversation_id:
                    memory_manager().ensure_conversation(user_id, conversation_id)
                emit_event("QUERY_STARTED", thread_id=run_id,
                           node_input={"question": payload.question, "run_id": run_id, "user_id": user_id,
                                       "max_replans": payload.max_replans,
                                       "web_search_enabled": payload.web_search_enabled,
                                       "memory_enabled": payload.memory_enabled,
                                       "experience_memory_enabled": payload.experience_memory_enabled,
                                       "requested_skill": payload.requested_skill,
                                       "conversation_id": conversation_id})
                initial = {"user_id": user_id, "thread_id": run_id, "question": payload.question,
                           "replan_count": 0, "max_replans": payload.max_replans,
                           "web_search_enabled": payload.web_search_enabled,
                           "conversation_id": conversation_id, "memory_enabled": payload.memory_enabled,
                           "experience_memory_enabled": payload.experience_memory_enabled,
                           "requested_skill": payload.requested_skill,
                           "skill_input": payload.skill_input,
                           "resolved_entities": {}, "task_history": []}
                runs.submit(run_id, lambda: graph.invoke(initial, config=_config(run_id)), trace_context=trace_context)
                submitted = True
    except Exception as exc:
        runs.fail(run_id, exc)
        if not submitted:
            finish_run_trace(trace_context, "FAILED")
        raise
    return JSONResponse(status_code=202, content={"run_id": run_id, "thread_id": run_id,
                                                   "user_id": user_id,
                                                   "conversation_id": conversation_id,
                                                   "memory_enabled": payload.memory_enabled,
                                                   "experience_memory_enabled": payload.experience_memory_enabled,
                                                   "trace_id": trace_context.trace_id, "status": "RUNNING"})


def _validate_conversation_id(conversation_id: str) -> None:
    if not 8 <= len(conversation_id) <= 128:
        raise HTTPException(status_code=422, detail="conversation_id 长度必须在 8 到 128 之间")


@app.get("/conversations/{conversation_id}/memory")
def get_conversation_memory(conversation_id: str, request: Request) -> dict:
    _validate_conversation_id(conversation_id)
    user_id = str(_authenticated_user(request)["user_id"])
    manager = memory_manager()
    if not manager.conversation_exists(user_id, conversation_id):
        raise HTTPException(status_code=404, detail="会话记忆不存在")
    memory = manager.get_conversation(user_id, conversation_id)
    # API 只返回上下文元数据；历史最终答案保留在存储中但不在此接口批量暴露。
    return {key: value for key, value in memory.items() if key != "turns"} | {
        "recent_turns": memory.get("turns", [])
    }


@app.delete("/conversations/{conversation_id}/memory")
def clear_conversation_memory(conversation_id: str, request: Request) -> dict:
    _validate_conversation_id(conversation_id)
    user_id = str(_authenticated_user(request)["user_id"])
    manager = memory_manager()
    if not manager.conversation_exists(user_id, conversation_id):
        raise HTTPException(status_code=404, detail="会话记忆不存在")
    result = manager.clear_memory(user_id, conversation_id=conversation_id)
    emit_event("MEMORY_CLEARED", user_id=user_id, conversation_id=conversation_id,
               deleted_turns=result["deleted_turns"], deleted_entities=result["deleted_entities"])
    return result


@app.get("/memory/categories")
def get_memory_categories(request: Request) -> dict:
    _authenticated_user(request)
    return {"categories": list(MEMORY_CATEGORIES)}


@app.get("/memory/summary")
def get_memory_summary(request: Request) -> dict:
    user_id = str(_authenticated_user(request)["user_id"])
    return memory_summary(user_id)


@app.get("/memory/facts")
def get_memory_facts(request: Request, query: str = "",
                     category: MemoryCategory | None = None,
                     include_archived: bool = False,
                     limit: int = 100) -> dict:
    user_id = str(_authenticated_user(request)["user_id"])
    facts = list_user_facts(
        user_id, query=query[:500], category=category,
        include_archived=include_archived,
        limit=max(1, min(limit, 500)),
    )
    return {"facts": facts, "count": len(facts), "user_id": user_id}


@app.post("/memory/facts", status_code=201)
def create_memory_fact(payload: MemoryFactCreateRequest,
                       request: Request) -> dict:
    user_id = str(_authenticated_user(request)["user_id"])
    try:
        fact = create_manual_fact(
            user_id, payload.content, payload.category, payload.confidence,
            payload.expected_valid_until,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"fact": fact}


@app.patch("/memory/facts/{fact_id}")
def update_memory_fact(fact_id: str, payload: MemoryFactUpdateRequest,
                       request: Request) -> dict:
    user_id = str(_authenticated_user(request)["user_id"])
    changes = payload.model_dump(exclude_unset=True)
    expected_revision = int(changes.pop("expected_revision"))
    if not changes:
        raise HTTPException(status_code=422, detail="至少提供一个待修改字段")
    try:
        fact = update_manual_fact(
            user_id, fact_id, changes, expected_revision=expected_revision
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="记忆事实不存在") from exc
    except MemoryRevisionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"fact": fact}


@app.post("/memory/facts/{fact_id}/review")
def review_memory_fact(fact_id: str, payload: MemoryFactReviewRequest,
                       request: Request) -> dict:
    user_id = str(_authenticated_user(request)["user_id"])
    try:
        fact = memory_manager().review_fact(
            user_id, fact_id, payload.action, payload.expected_revision,
            payload.review_days,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="记忆事实不存在") from exc
    except MemoryRevisionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"fact": fact}


@app.delete("/memory/facts/{fact_id}")
def delete_memory_fact(fact_id: str, request: Request,
                       expected_revision: int) -> dict:
    user_id = str(_authenticated_user(request)["user_id"])
    try:
        deleted = delete_manual_fact(user_id, fact_id, expected_revision)
    except MemoryRevisionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="记忆事实不存在")
    return {"deleted": True, "fact_id": fact_id}


@app.get("/memory/export")
def export_memory(request: Request) -> JSONResponse:
    user_id = str(_authenticated_user(request)["user_id"])
    return JSONResponse(
        content=json.loads(json.dumps(export_user_memory(user_id), default=str)),
        headers={
            "Content-Disposition":
                f'attachment; filename="user-memory-{user_id}.json"'
        },
    )


@app.get("/memory/audit")
def get_memory_audit(request: Request, limit: int = 100) -> dict:
    user_id = str(_authenticated_user(request)["user_id"])
    logs = memory_manager().list_audit_logs(user_id, max(1, min(limit, 500)))
    return {"audit_logs": logs, "count": len(logs), "user_id": user_id}


@app.delete("/memory")
def clear_all_memory(payload: ClearAllMemoryRequest,
                     request: Request) -> dict:
    user_id = str(_authenticated_user(request)["user_id"])
    result = memory_manager().clear_all_personal_memory(user_id)
    emit_event("ALL_PERSONAL_MEMORY_CLEARED", user_id=user_id, **{
        key: value for key, value in result.items() if key.startswith("deleted_")
    })
    return result


@app.get("/experience-memory/stats")
def get_query_experience_stats(request: Request) -> dict:
    user_id = str(_authenticated_user(request)["user_id"])
    return query_experience_stats(user_id)


@app.get("/experience-memory/patterns")
def get_query_experience_patterns(request: Request, limit: int = 50) -> dict:
    user_id = str(_authenticated_user(request)["user_id"])
    settings = Settings.from_env()
    bounded_limit = max(1, min(limit, 200))
    manager = memory_manager()
    patterns = (
        manager.list_experience_patterns("user", user_id, bounded_limit)
        + manager.list_experience_patterns(
            "global", settings.query_experience_scope_id, bounded_limit
        )
    )
    patterns.sort(key=lambda row: (
        0 if row["scope_type"] == "user" else 1,
        -row["success_count"], row["pattern_id"],
    ))
    patterns = patterns[:bounded_limit]
    return {"patterns": patterns, "count": len(patterns),
            "user_id": user_id, "global_scope_id": settings.query_experience_scope_id,
            "mode": settings.query_experience_mode}


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
