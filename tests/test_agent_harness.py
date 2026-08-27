import json
import time

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from agents.harness import (
    AgentHarness,
    HarnessConfig,
    HarnessMiddleware,
    ToolErrorCategory,
    classify_tool_error,
)
from agents.verification_agent import VerificationAgent
from models.settings import Settings
from services.observability import clear_events, get_events


def _messages():
    return [SystemMessage(content="test"), HumanMessage(content="{}")]


def test_harness_runs_middleware_chain_in_order():
    seen = []

    class Probe(HarnessMiddleware):
        def before_run(self, context, messages):
            seen.append("before_run")

        def before_model(self, context, messages):
            seen.append("before_model")

        def after_model(self, context, message):
            seen.append("after_model")

        def after_run(self, context, result):
            seen.append("after_run")

    class FinalModel:
        def bind_tools(self, tools):
            return self

        def invoke(self, messages):
            return AIMessage(content="done")

    result = AgentHarness(
        "probe_agent", FinalModel(), [], middleware=[Probe()]
    ).execute(_messages())
    assert result.final_response == "done"
    assert seen == ["before_run", "before_model", "after_model", "after_run"]


def test_harness_detects_repeated_tool_loop_before_third_execution():
    counter = {"value": 0}

    @tool
    def echo(value: int) -> dict:
        """Echo a value."""
        counter["value"] += 1
        return {"value": value}

    class LoopModel:
        def bind_tools(self, tools):
            return self

        def invoke(self, messages):
            index = len(
                [message for message in messages if isinstance(message, ToolMessage)]
            )
            return AIMessage(
                content="loop",
                tool_calls=[
                    {
                        "name": "echo",
                        "args": {"value": 1},
                        "id": f"loop-{index}",
                        "type": "tool_call",
                    }
                ],
            )

    config = HarnessConfig(
        max_steps=8, max_tool_calls=8, loop_repeat_threshold=3, tool_max_retries=0
    )
    result = AgentHarness("loop_agent", LoopModel(), [echo], config=config).execute(
        _messages()
    )
    assert counter["value"] == 2
    assert result.stop_reason == "AGENT_LOOP_DETECTED"
    assert "重复工具调用" in result.errors[0]


def test_harness_classifies_and_retries_transient_tool_errors():
    thread_id = "harness-retry"
    clear_events(thread_id)
    counter = {"value": 0}

    @tool
    def flaky() -> dict:
        """Fail once, then succeed."""
        counter["value"] += 1
        if counter["value"] == 1:
            raise ConnectionError("provider unavailable")
        return {"ok": True}

    class OnceModel:
        def bind_tools(self, tools):
            return self

        def invoke(self, messages):
            if any(isinstance(message, ToolMessage) for message in messages):
                return AIMessage(content="done")
            return AIMessage(
                content="call",
                tool_calls=[
                    {
                        "name": "flaky",
                        "args": {},
                        "id": "flaky-1",
                        "type": "tool_call",
                    }
                ],
            )

    config = HarnessConfig(tool_timeout_seconds=1, tool_max_retries=1)
    result = AgentHarness("retry_agent", OnceModel(), [flaky], config=config).execute(
        _messages(), thread_id
    )
    assert counter["value"] == 2
    assert result.observations[0]["success"] is True
    assert result.observations[0]["attempts"] == 2
    assert "AGENT_TOOL_RETRYING" in [event["event"] for event in get_events(thread_id)]
    assert (
        classify_tool_error(ValueError("bad args"))
        == ToolErrorCategory.INVALID_ARGUMENT
    )


def test_harness_enforces_tool_timeout_and_finite_retry():
    @tool
    def slow() -> dict:
        """Sleep longer than the configured deadline."""
        time.sleep(0.05)
        return {"ok": True}

    class OnceModel:
        def bind_tools(self, tools):
            return self

        def invoke(self, messages):
            if any(isinstance(message, ToolMessage) for message in messages):
                return AIMessage(content="done")
            return AIMessage(
                content="call",
                tool_calls=[
                    {
                        "name": "slow",
                        "args": {},
                        "id": "slow-1",
                        "type": "tool_call",
                    }
                ],
            )

    config = HarnessConfig(tool_timeout_seconds=0.005, tool_max_retries=1)
    result = AgentHarness("timeout_agent", OnceModel(), [slow], config=config).execute(
        _messages()
    )
    assert result.observations[0]["error_category"] == "TIMEOUT"
    assert result.observations[0]["attempts"] == 2
    assert len(result.errors) == 1


def test_harness_compresses_only_model_observation_not_raw_result():
    captured = {}

    @tool
    def large_result() -> list[dict]:
        """Return a deliberately large observation."""
        return [{"index": index, "text": "x" * 40} for index in range(100)]

    class CaptureModel:
        def bind_tools(self, tools):
            return self

        def invoke(self, messages):
            tool_messages = [
                message for message in messages if isinstance(message, ToolMessage)
            ]
            if tool_messages:
                captured["observation"] = json.loads(tool_messages[-1].content)
                return AIMessage(content="done")
            return AIMessage(
                content="call",
                tool_calls=[
                    {
                        "name": "large_result",
                        "args": {},
                        "id": "large-1",
                        "type": "tool_call",
                    }
                ],
            )

    config = HarnessConfig(
        observation_max_chars=300, observation_max_items=3, tool_max_retries=0
    )
    result = AgentHarness(
        "compression_agent", CaptureModel(), [large_result], config=config
    ).execute(_messages())
    assert len(result.observations[0]["data"]) == 100
    assert captured["observation"] != result.observations[0]["data"]
    assert (
        isinstance(captured["observation"], dict)
        and captured["observation"].get("_observation_compressed")
    ) or len(captured["observation"]) <= 4


def test_harness_stops_when_model_token_budget_is_exceeded():
    class CostlyModel:
        def bind_tools(self, tools):
            return self

        def invoke(self, messages):
            return AIMessage(
                content="done",
                usage_metadata={
                    "input_tokens": 7,
                    "output_tokens": 6,
                    "total_tokens": 13,
                },
            )

    config = HarnessConfig(max_tokens=10)
    result = AgentHarness("budget_agent", CostlyModel(), [], config=config).execute(
        _messages()
    )
    assert result.stop_reason == "AGENT_BUDGET_EXCEEDED"
    assert result.metrics["total_tokens"] == 13


def test_verification_agent_reuses_shared_harness():
    assert isinstance(VerificationAgent().harness, AgentHarness)


def test_agent_model_configuration_overrides_global_settings(monkeypatch):
    monkeypatch.setenv("TALENT_AGENT_MODEL_PROVIDER", "openai")
    monkeypatch.setenv("TALENT_AGENT_MODEL_NAME", "agent-specific-model")
    monkeypatch.setenv("TALENT_AGENT_MODEL_API_KEY", "test-key")
    monkeypatch.setenv("TALENT_AGENT_MODEL_INPUT_COST_PER_MILLION", "3.5")
    config = Settings.from_env().model_config("talent_agent")
    assert config.provider == "openai"
    assert config.name == "agent-specific-model"
    assert config.api_key == "test-key"
    assert config.input_cost_per_million == 3.5
