import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage
from agents.achievement_agent import build_achievement_agent
from app.main import app
from models.llm import ModelFactory, MockToolCallingModel
from models.settings import Settings
from nodes.supervisor_node import supervisor_node


def test_domain_agent_uses_multi_round_toolmessage_loop():
    class RecordingModel(MockToolCallingModel):
        seen_tool_counts: list[int] = []

        def invoke(self, messages):
            self.seen_tool_counts.append(sum(isinstance(item, ToolMessage) for item in messages))
            return super().invoke(messages)

    agent = build_achievement_agent()
    recording = RecordingModel("achievement")
    agent.model = recording.bind_tools(list(agent.tools.values()))
    result = agent.run("查询共同科研成果", {"张伟": "person_zw_001", "李明": "person_lm_001"})
    assert recording.seen_tool_counts == [0, 1, 2, 3]
    assert [item["name"] for item in result["tool_calls"]] == [
        "get_common_papers", "get_common_projects", "aggregate_cooperation"
    ]
    assert result["errors"] == []


def test_domain_agent_stops_when_model_returns_no_tool_calls():
    class ImmediateFinalModel:
        def bind_tools(self, tools):
            return self

        def invoke(self, messages):
            return AIMessage(content='{"status":"complete"}')

    agent = build_achievement_agent()
    agent.model = ImmediateFinalModel()
    result = agent.run("无需工具", {})
    assert result["tool_calls"] == []
    assert result["facts"] == []


def test_replan_only_schedules_missing_domain():
    state = {
        "question": "综合分析学术、职业和企业关系",
        "resolved_entities": {"张伟": "person_zw_001", "李明": "person_lm_001"},
        "validation_result": {"valid": False, "needs_replan": True, "missing_domains": ["enterprise"], "errors": []},
        "task_history": [{"agent": "achievement_agent", "status": "completed"}],
        "replan_count": 0,
        "max_replans": 2,
    }
    update = supervisor_node(state)
    assert [task["agent"] for task in update["tasks"]] == ["enterprise_agent"]
    assert update["replan_count"] == 1
    assert "最小化重规划" in update["plan"]["reason"]


def test_openai_provider_requires_api_key(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "openai")
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ZHIPUAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="必须设置 MODEL_API_KEY"):
        ModelFactory.tool_calling_model("achievement")


def test_auto_provider_selects_glm_when_zhipu_key_exists(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "auto")
    monkeypatch.setenv("ZHIPUAI_API_KEY", "test-only-key")
    monkeypatch.delenv("MODEL_NAME", raising=False)
    monkeypatch.delenv("MODEL_BASE_URL", raising=False)
    settings = Settings.from_env()
    assert settings.model_provider == "openai"
    assert settings.model_name == "glm-5.2"
    assert settings.model_base_url == "https://open.bigmodel.cn/api/paas/v4/"


def test_persistent_api_interrupt_resume_state_and_history():
    client = TestClient(app)
    thread_id = "stage4-api-resume"
    created = client.post("/queries", json={
        "question": "张伟发表过哪些论文？",
        "thread_id": thread_id,
        "max_replans": 2,
    })
    assert created.status_code == 200
    assert created.json()["status"] == "NEED_USER_SELECTION"
    resumed = client.post(f"/queries/{thread_id}/resume", json={"selections": {"张伟": "person_zw_001"}})
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "COMPLETED"
    assert resumed.json()["state"]["validation_result"]["valid"] is True
    current = client.get(f"/queries/{thread_id}")
    assert current.status_code == 200
    assert current.json()["state"]["resolved_entities"]["张伟"] == "person_zw_001"
    history = client.get(f"/queries/{thread_id}/history?limit=50")
    assert history.status_code == 200
    assert len(history.json()["history"]) >= 2


def test_api_unknown_thread_returns_404():
    client = TestClient(app)
    assert client.get("/queries/thread-does-not-exist").status_code == 404
    assert client.post("/queries/thread-does-not-exist/resume", json={"selections": {}}).status_code == 404
