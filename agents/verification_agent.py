"""复杂语义 Verification Agent：局部 Messages 驱动真正的多轮 Tool Calling Loop。"""
import json
import logging
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage
from models.llm import ModelFactory
from models.schemas import VerificationResult, ToolCallSpec
from tools.verification_tools import verify_evidence, check_source, validate_relation, check_constraints, get_cooperation_timeline

logger = logging.getLogger(__name__)


class VerificationAgent:
    def __init__(self, max_steps: int = 8):
        tools = [verify_evidence, check_source, validate_relation, check_constraints, get_cooperation_timeline]
        self.tools = {tool.name: tool for tool in tools}
        self.model = ModelFactory.verification_model().bind_tools(tools)
        self.max_steps = max_steps

    def run(self, question: str, entity_ids: list[str], evidence_ids: list[str]) -> dict:
        messages = [
            SystemMessage(content="你是证据验证 Agent。必须调用工具验证证据、来源、时间线、关系和约束，禁止使用模型自身知识。"),
            HumanMessage(content=json.dumps({"question": question, "entity_ids": entity_ids,
                                             "evidence_ids": evidence_ids}, ensure_ascii=False)),
        ]
        calls, observations = [], []
        for step in range(self.max_steps):
            response = self.model.invoke(messages)
            messages.append(response)
            if not response.tool_calls:
                payload = json.loads(response.content)
                return VerificationResult(**payload, tool_calls=calls, observations=observations).model_dump()
            for call in response.tool_calls:
                calls.append(ToolCallSpec(name=call["name"], arguments=call["args"]))
                tool = self.tools.get(call["name"])
                if tool is None:
                    observation = {"error": f"工具未授权: {call['name']}"}
                else:
                    try:
                        logger.info("verification_agent 第 %s 轮调用 %s", step + 1, call["name"])
                        observation = tool.invoke(call["args"])
                    except Exception as exc:
                        logger.exception("Verification 工具失败")
                        observation = {"error": str(exc)}
                observations.append({"tool": call["name"], "data": observation})
                messages.append(ToolMessage(content=json.dumps(observation, ensure_ascii=False),
                                            tool_call_id=call["id"], name=call["name"]))
        return VerificationResult(status="FAIL", relation="CORE_RESEARCH_PARTNER", confidence=0,
                                  reason="VerificationAgent 达到最大工具调用轮数", needs_replan=True,
                                  missing_evidence=["verification_loop_completion"], tool_calls=calls,
                                  observations=observations).model_dump()


def build_verification_agent() -> VerificationAgent:
    return VerificationAgent()
