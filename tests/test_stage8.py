import time
from threading import Event
from types import SimpleNamespace

from models.settings import Settings
from repositories.run_repository import SQLiteRunRepository
from services.entity_service import EntityService
from services.run_control import raise_if_stopped
from services.run_service import RunManager
from graph.builder import build_graph
from langgraph.types import Command


class FakeExactRepository:
    backend = "mysql"

    def search_scholars(self, mention, limit=10):
        return [
            {"entity_id": "p1", "name": "张伟", "organization": "清华大学", "title": "教授"},
            {"entity_id": "p2", "name": "张伟", "organization": "北京理工大学", "title": "研究员"},
        ] if mention == "张伟" else []

    def get_scholar(self, entity_id):
        return None


class FakeVectorRepository:
    backend = "milvus"

    def search_scholars(self, query, limit=10):
        if "张伟" not in query:
            return []
        return [
            {"entity_id": "p1", "name": "张伟", "organization": "清华大学", "title": "教授"},
            {"entity_id": "p2", "name": "张伟", "organization": "北京理工大学", "title": "研究员"},
        ]

    def get_scholar(self, entity_id):
        return None


def test_hybrid_entity_search_fuses_mysql_milvus_and_context(monkeypatch):
    monkeypatch.setenv("ENTITY_AUTO_RESOLVE_THRESHOLD", "0.9")
    monkeypatch.setenv("ENTITY_SCORE_GAP_THRESHOLD", "0.15")
    service = EntityService(exact_repository=FakeExactRepository(), vector_repository=FakeVectorRepository(), backend="hybrid")
    rows = service.search("张伟", "清华大学教授张伟发表过哪些论文")
    assert rows[0]["entity_id"] == "p1"
    assert rows[0]["retrieval_method"] == "mysql+milvus+rrf"
    assert "问题上下文命中机构" in rows[0]["match_reasons"]
    assert service.auto_resolve(rows) == "p1"


def test_hybrid_entity_search_returns_empty_for_unknown():
    service = EntityService(exact_repository=FakeExactRepository(), vector_repository=FakeVectorRepository(), backend="hybrid")
    assert service.search("不存在", "不存在") == []


def test_run_registry_survives_manager_restart(tmp_path):
    path = str(tmp_path / "runs.sqlite")
    manager = RunManager(repository=SQLiteRunRepository(path), timeout_seconds=1)
    manager.create("persistent-run")
    manager.submit("persistent-run", lambda: {"final_answer": "ok"})
    deadline = time.monotonic() + 1
    while manager.get("persistent-run")["status"] == "RUNNING" and time.monotonic() < deadline:
        time.sleep(0.005)
    assert manager.get("persistent-run")["status"] == "COMPLETED"
    manager.close()
    repository = SQLiteRunRepository(path)
    assert repository.get("persistent-run")["status"] == "COMPLETED"
    repository.close()


def test_run_registry_persists_entity_not_found_interrupt(tmp_path):
    path = str(tmp_path / "interrupt.sqlite")
    manager = RunManager(repository=SQLiteRunRepository(path), timeout_seconds=1)
    manager.create("not-found-run")
    interrupt = {"status": "ENTITY_NOT_FOUND", "mentions": ["王强"]}
    manager.submit("not-found-run", lambda: {"__interrupt__": [SimpleNamespace(value=interrupt)]})
    deadline = time.monotonic() + 1
    while manager.get("not-found-run")["status"] == "RUNNING" and time.monotonic() < deadline:
        time.sleep(0.005)
    assert manager.get("not-found-run")["status"] == "ENTITY_NOT_FOUND"
    manager.close()
    repository = SQLiteRunRepository(path)
    assert repository.get("not-found-run")["interrupt"] == interrupt
    repository.close()


def test_run_timeout_is_cooperative(tmp_path):
    manager = RunManager(repository=SQLiteRunRepository(str(tmp_path / "timeout.sqlite")), timeout_seconds=0.01)
    manager.create("timeout-run")

    def operation():
        time.sleep(0.03)
        raise_if_stopped("timeout-run")
        return {}

    manager.submit("timeout-run", operation)
    deadline = time.monotonic() + 1
    while manager.get("timeout-run")["status"] not in RunManager.TERMINAL and time.monotonic() < deadline:
        time.sleep(0.005)
    assert manager.get("timeout-run")["status"] == "TIMED_OUT"
    manager.close()


def test_queued_run_can_be_cancelled(tmp_path):
    manager = RunManager(max_workers=1, repository=SQLiteRunRepository(str(tmp_path / "cancel.sqlite")), timeout_seconds=1)
    blocker = Event()
    def blocked_operation():
        blocker.wait(0.5)
        return {}
    manager.create("blocking-run")
    manager.submit("blocking-run", blocked_operation)
    manager.create("queued-run")
    manager.submit("queued-run", lambda: {})
    assert manager.cancel("queued-run") is True
    assert manager.get("queued-run")["status"] == "CANCELLED"
    blocker.set()
    deadline = time.monotonic() + 1
    while manager.get("blocking-run")["status"] == "RUNNING" and time.monotonic() < deadline:
        time.sleep(0.005)
    manager.close()


def test_single_patent_query_uses_patent_tool_and_answers_question():
    graph = build_graph()
    config = {"configurable": {"thread_id": "stage8-single-patent"}}
    first = graph.invoke({"question": "张伟有哪些专利？", "max_replans": 2, "replan_count": 0}, config=config)
    final = graph.invoke(Command(resume={"张伟": "person_zw_001"}), config=config)
    tools = {fact["tool"] for fact in final["achievement_result"]["facts"]}
    assert tools == {"get_person_patents"}
    assert "知识图谱协同推理方法" in final["final_answer"]


def test_education_query_uses_education_tool():
    graph = build_graph()
    config = {"configurable": {"thread_id": "stage8-education"}}
    graph.invoke({"question": "张伟的教育经历是什么？", "max_replans": 2, "replan_count": 0}, config=config)
    final = graph.invoke(Command(resume={"张伟": "person_zw_001"}), config=config)
    tools = {fact["tool"] for fact in final["talent_result"]["facts"]}
    assert tools == {"get_education_history"}
    assert "教育经历" in final["final_answer"]
