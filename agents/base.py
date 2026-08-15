"""最小 Tool-Calling Agent 循环，严格限制每个 Agent 可见工具。"""
import logging
import json
from typing import Any
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage
from models.schemas import DomainResult, ToolCallSpec
from services.observability import emit_event

logger = logging.getLogger(__name__)


class ToolCallingDomainAgent:
    def __init__(self, name: str, model: Any, tools: list[Any], max_steps: int = 12):
        self.name = name
        self.model = model.bind_tools(tools)
        self.tools = {item.name: item for item in tools}
        self.max_steps = max_steps

    def run(self, goal: str, resolved_entities: dict[str, str], thread_id: str | None = None) -> dict:
        facts, evidence, errors, calls = [], [], [], []
        relation_instruction = ("当前包含多个已解析实体，目标是分析实体之间的关系。优先调用共同、重叠、合作、"
                                "路径或聚合类工具取得直接关系证据；不要只返回彼此独立的个人资料。"
                                if len(resolved_entities) > 1 else "当前是单实体查询，调用面向单个实体的工具。")
        messages = [
            SystemMessage(content=f"你是 {self.name}。只使用已绑定工具完成目标；每次根据 ToolMessage 决定下一步，"
                                  f"完成后返回无 tool_calls 的消息。{relation_instruction}必须取得足以回答目标的证据后才能结束。"),
            HumanMessage(content=json.dumps({"goal": goal, "resolved_entities": resolved_entities}, ensure_ascii=False)),
        ]
        for step in range(self.max_steps):
            message = self.model.invoke(messages)
            messages.append(message)
            if not message.tool_calls:
                break
            for call in message.tool_calls:
                calls.append(ToolCallSpec(name=call["name"], arguments=call["args"]))
                tool = self.tools.get(call["name"])
                try:
                    if tool is None:
                        raise ValueError(f"工具未授权: {call['name']}")
                    logger.info("%s 调用工具 %s", self.name, call["name"])
                    emit_event("AGENT_TOOL_CALLED", thread_id=thread_id, agent_name=self.name,
                               tool_name=call["name"], step=step + 1, tool_input=call["args"])
                    output = tool.invoke(call["args"])
                    emit_event("AGENT_TOOL_COMPLETED", thread_id=thread_id, agent_name=self.name,
                               tool_name=call["name"], step=step + 1,
                               result_count=len(output) if isinstance(output, list) else 1,
                               tool_output=output)
                    facts.append({"tool": call["name"], "data": output})
                    rows = output if isinstance(output, list) else [output]
                    for row in rows:
                        if isinstance(row, dict):
                            ids = row.get("evidence_ids", [row.get("evidence_id")])
                            evidence.extend({"evidence_id": x, "source_tool": call["name"]} for x in ids if x)
                except Exception as exc:
                    logger.exception("%s 工具调用失败", self.name)
                    output = {"error": str(exc)}
                    errors.append(f"{call['name']}: {exc}")
                messages.append(ToolMessage(content=json.dumps(output, ensure_ascii=False),
                                            tool_call_id=call["id"], name=call["name"]))
        else:
            errors.append(f"达到最大工具调用轮数: {self.max_steps}")
        summary = f"{self.name} 完成 {len(calls)} 次工具调用，得到 {len(facts)} 组结果"
        emit_event("AGENT_COMPLETED", thread_id=thread_id, agent_name=self.name,
                   tool_call_count=len(calls), fact_count=len(facts), error_count=len(errors))
        return DomainResult(agent=self.name, summary=summary, facts=facts, evidence=evidence, tool_calls=calls, errors=errors).model_dump()
