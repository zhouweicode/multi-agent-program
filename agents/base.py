"""领域 Agent 适配器；通用执行能力由 AgentHarness 提供。"""

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from agents.harness import AgentHarness, HarnessConfig, HarnessMiddleware
from models.schemas import DomainResult
from services.evidence_normalizer import normalize_tool_output
from services.telemetry import traced_span


class ToolCallingDomainAgent(AgentHarness):
    """保持原公开接口，并把业务结果组装与通用 Harness 执行解耦。"""

    def __init__(
        self,
        name: str,
        model: Any,
        tools: list[Any],
        max_steps: int = 12,
        max_tool_calls: int = 16,
        final_response_instruction: str = "",
        middleware: list[HarnessMiddleware] | None = None,
        harness_config: HarnessConfig | None = None,
    ):
        config = harness_config or HarnessConfig.from_env(
            name,
            max_steps=max_steps,
            max_tool_calls=max_tool_calls,
        )
        super().__init__(name, model, tools, config=config, middleware=middleware)
        self.max_steps = config.max_steps
        self.max_tool_calls = config.max_tool_calls
        self.final_response_instruction = final_response_instruction

    def run(
        self, goal: str, resolved_entities: dict[str, str], thread_id: str | None = None
    ) -> dict:
        with traced_span(
            f"agent.{self.name}",
            "agent",
            {
                "agent.name": self.name,
                "agent.entity_count": len(resolved_entities),
            },
        ) as span:
            result = self._run_impl(goal, resolved_entities, thread_id)
            span.set_attribute(
                "agent.tool_call_count", len(result.get("tool_calls", []))
            )
            span.set_attribute("agent.error_count", len(result.get("errors", [])))
            return result

    def _run_impl(
        self, goal: str, resolved_entities: dict[str, str], thread_id: str | None = None
    ) -> dict:
        relation_instruction = (
            "当前包含多个已解析实体，目标是分析实体之间的关系。优先调用共同、重叠、合作、"
            "路径或聚合类工具取得直接关系证据；不要只返回彼此独立的个人资料。"
            if len(resolved_entities) > 1
            else "当前是单实体查询，调用面向单个实体的工具。"
        )
        messages = [
            SystemMessage(
                content=(
                    f"你是 {self.name}。只使用已绑定工具完成目标；每次根据 ToolMessage 决定下一步，"
                    f"完成后返回无 tool_calls 的消息。{relation_instruction}必须取得足以回答目标的证据后才能结束。"
                    f"整个任务最多调用 {self.max_tool_calls} 次工具；先检索 ID，再只查询最相关的少量对象，禁止穷举全部节点。"
                    f"{self.final_response_instruction}"
                )
            ),
            HumanMessage(
                content=json.dumps(
                    {"goal": goal, "resolved_entities": resolved_entities},
                    ensure_ascii=False,
                )
            ),
        ]
        run = self.execute(messages, thread_id)
        facts: list[dict[str, Any]] = []
        evidence: list[dict[str, Any]] = []
        errors = list(run.errors)
        entity_ids = list(resolved_entities.values())
        for observation in run.observations:
            if not observation.get("success"):
                continue
            tool_name, output = observation["tool"], observation.get("data")
            facts.append({"tool": tool_name, "data": output})
            try:
                evidence.extend(normalize_tool_output(tool_name, output, entity_ids))
            except Exception as exc:  # noqa: BLE001 - 规范化器属于插件边界，错误必须转为 Agent 结果。
                errors.append(f"{tool_name}: [OBSERVATION_NORMALIZATION_ERROR] {exc}")
        summary = f"{self.name} 完成 {len(run.tool_calls)} 次工具调用，得到 {len(facts)} 组结果"
        return DomainResult(
            agent=self.name,
            summary=summary,
            response=run.final_response,
            facts=facts,
            evidence=evidence,
            tool_calls=run.tool_calls,
            errors=errors,
            metrics=run.metrics,
            stop_reason=run.stop_reason,
        ).model_dump()
