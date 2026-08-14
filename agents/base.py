"""最小 Tool-Calling Agent 循环，严格限制每个 Agent 可见工具。"""
import logging
from typing import Any
from models.schemas import DomainResult, ToolCallSpec

logger = logging.getLogger(__name__)


class ToolCallingDomainAgent:
    def __init__(self, name: str, model: Any, tools: list[Any]):
        self.name = name
        self.model = model.bind_tools(tools)
        self.tools = {item.name: item for item in tools}

    def run(self, goal: str, resolved_entities: dict[str, str]) -> dict:
        message = self.model.invoke({"goal": goal, "resolved_entities": resolved_entities})
        facts, evidence, errors, calls = [], [], [], []
        for call in message.tool_calls:
            calls.append(ToolCallSpec(name=call["name"], arguments=call["args"]))
            tool = self.tools.get(call["name"])
            try:
                if tool is None:
                    raise ValueError(f"工具未授权: {call['name']}")
                logger.info("%s 调用工具 %s", self.name, call["name"])
                output = tool.invoke(call["args"])
                facts.append({"tool": call["name"], "data": output})
                rows = output if isinstance(output, list) else [output]
                for row in rows:
                    if isinstance(row, dict):
                        ids = row.get("evidence_ids", [row.get("evidence_id")])
                        evidence.extend({"evidence_id": x, "source_tool": call["name"]} for x in ids if x)
            except Exception as exc:  # 教学项目保留清晰错误，不吞异常上下文
                logger.exception("%s 工具调用失败", self.name)
                errors.append(f"{call['name']}: {exc}")
        summary = f"{self.name} 完成 {len(calls)} 次工具调用，得到 {len(facts)} 组结果"
        return DomainResult(agent=self.name, summary=summary, facts=facts, evidence=evidence, tool_calls=calls, errors=errors).model_dump()

