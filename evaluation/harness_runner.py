"""Agent Harness 与故障注入的确定性评测。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool

from agents.harness import AgentHarness, HarnessConfig, HarnessMiddleware


def load_harness_cases(path: str | Path) -> list[dict[str, Any]]:
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise TypeError("Harness 评测集必须是 JSON 数组")
    ids = [row.get("case_id") for row in rows]
    if any(not item for item in ids) or len(ids) != len(set(ids)):
        raise ValueError("Harness 评测 case_id 必须存在且唯一")
    return rows


def _messages() -> list[Any]:
    return [SystemMessage(content="harness evaluation"), HumanMessage(content="{}")]


class _FinalModel:
    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        return AIMessage(content="done")


class _SingleToolModel:
    def __init__(self, tool_name: str, capture: dict[str, Any] | None = None):
        self.tool_name = tool_name
        self.capture = capture

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        observations = [
            message for message in messages if isinstance(message, ToolMessage)
        ]
        if observations:
            if self.capture is not None:
                self.capture["observation"] = json.loads(observations[-1].content)
            return AIMessage(content="done")
        return AIMessage(
            content="call",
            tool_calls=[
                {
                    "name": self.tool_name,
                    "args": {},
                    "id": "eval-call",
                    "type": "tool_call",
                }
            ],
        )


def _tool(name: str, function) -> StructuredTool:
    return StructuredTool.from_function(
        function, name=name, description=f"Harness eval tool {name}"
    )


def _config(case: dict[str, Any]) -> HarnessConfig:
    return HarnessConfig(**case.get("config", {}))


def _middleware_chain(case: dict[str, Any]) -> dict[str, Any]:
    seen: list[str] = []

    class Probe(HarnessMiddleware):
        def before_run(self, context, messages):
            seen.append("before_run")

        def before_model(self, context, messages):
            seen.append("before_model")

        def after_model(self, context, message):
            seen.append("after_model")

        def after_run(self, context, result):
            seen.append("after_run")

    AgentHarness(
        "eval_middleware_agent",
        _FinalModel(),
        [],
        config=_config(case),
        middleware=[Probe()],
    ).execute(_messages())
    return {
        "passed": seen == ["before_run", "before_model", "after_model", "after_run"]
    }


def _repeated_loop(case: dict[str, Any]) -> dict[str, Any]:
    counter = {"value": 0}

    def echo() -> dict:
        counter["value"] += 1
        return {"ok": True}

    class LoopModel(_SingleToolModel):
        def invoke(self, messages):
            index = len(
                [message for message in messages if isinstance(message, ToolMessage)]
            )
            return AIMessage(
                content="loop",
                tool_calls=[
                    {
                        "name": "eval_echo",
                        "args": {},
                        "id": f"loop-{index}",
                        "type": "tool_call",
                    }
                ],
            )

    result = AgentHarness(
        "eval_loop_agent",
        LoopModel("eval_echo"),
        [_tool("eval_echo", echo)],
        config=_config(case),
    ).execute(_messages())
    return {
        "stop_reason": result.stop_reason,
        "successful_executions": counter["value"],
    }


def _transient_error(case: dict[str, Any]) -> dict[str, Any]:
    counter = {"value": 0}

    def flaky() -> dict:
        counter["value"] += 1
        if counter["value"] == 1:
            raise ConnectionError("injected provider outage")
        return {"ok": True}

    result = AgentHarness(
        "eval_retry_agent",
        _SingleToolModel("eval_flaky"),
        [_tool("eval_flaky", flaky)],
        config=_config(case),
    ).execute(_messages())
    observation = result.observations[0]
    return {"success": observation["success"], "attempts": observation["attempts"]}


def _timeout(case: dict[str, Any]) -> dict[str, Any]:
    def slow() -> dict:
        time.sleep(0.05)
        return {"ok": True}

    result = AgentHarness(
        "eval_timeout_agent",
        _SingleToolModel("eval_slow"),
        [_tool("eval_slow", slow)],
        config=_config(case),
    ).execute(_messages())
    observation = result.observations[0]
    return {
        "error_category": observation["error_category"],
        "attempts": observation["attempts"],
    }


def _observation_compression(case: dict[str, Any]) -> dict[str, Any]:
    capture: dict[str, Any] = {}

    def large() -> list[dict[str, Any]]:
        return [{"index": index, "text": "x" * 40} for index in range(100)]

    result = AgentHarness(
        "eval_compression_agent",
        _SingleToolModel("eval_large", capture),
        [_tool("eval_large", large)],
        config=_config(case),
    ).execute(_messages())
    model_observation = capture["observation"]
    compressed = (
        isinstance(model_observation, dict)
        and model_observation.get("_observation_compressed") is True
    ) or (isinstance(model_observation, list) and len(model_observation) < 100)
    return {"raw_count": len(result.observations[0]["data"]), "compressed": compressed}


def _token_budget(case: dict[str, Any]) -> dict[str, Any]:
    class UsageModel(_FinalModel):
        def invoke(self, messages):
            return AIMessage(
                content="done",
                usage_metadata={
                    "input_tokens": 7,
                    "output_tokens": 6,
                    "total_tokens": 13,
                },
            )

    result = AgentHarness(
        "eval_budget_agent", UsageModel(), [], config=_config(case)
    ).execute(_messages())
    return {
        "stop_reason": result.stop_reason,
        "total_tokens": result.metrics["total_tokens"],
    }


_EVALUATORS = {
    "middleware_chain": _middleware_chain,
    "repeated_loop": _repeated_loop,
    "transient_error": _transient_error,
    "timeout": _timeout,
    "observation_compression": _observation_compression,
    "token_budget": _token_budget,
}


def evaluate_harness_dataset(path: str | Path) -> dict[str, Any]:
    rows = []
    for case in load_harness_cases(path):
        scenario = case.get("scenario")
        if scenario not in _EVALUATORS:
            raise ValueError(f"未知 Harness 评测场景: {scenario}")
        actual = _EVALUATORS[scenario](case)
        expected = case["expected"]
        checks = {key: actual.get(key) == value for key, value in expected.items()}
        rows.append(
            {
                "case_id": case["case_id"],
                "scenario": scenario,
                "passed": all(checks.values()),
                "checks": checks,
                "expected": expected,
                "actual": actual,
            }
        )
    passed = sum(row["passed"] for row in rows)
    return {
        "dataset": str(path),
        "case_count": len(rows),
        "passed": passed,
        "metrics": {"case_pass_rate": round(passed / len(rows), 4) if rows else None},
        "cases": rows,
    }


def evaluate_harness_gate(
    report: dict[str, Any], baseline: dict[str, Any]
) -> dict[str, Any]:
    failures = []
    if report["case_count"] != baseline.get("expected_case_count"):
        failures.append(
            f"评测数量应为 {baseline.get('expected_case_count')}，实际为 {report['case_count']}"
        )
    minimum = baseline.get("minimum_case_pass_rate", 1)
    if report["metrics"]["case_pass_rate"] < minimum:
        failures.append(f"case_pass_rate 低于门槛 {minimum}")
    return {"passed": not failures, "failures": failures}
