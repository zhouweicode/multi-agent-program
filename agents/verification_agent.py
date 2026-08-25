"""复杂语义 Verification Agent：局部 Messages 驱动真正的多轮 Tool Calling Loop。"""
import json
import logging
import re
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

    @staticmethod
    def _parse_result(content) -> dict:
        """兼容纯 JSON、Markdown JSON 代码块及 LangChain 内容块。"""
        if isinstance(content, dict):
            return content
        if isinstance(content, list):
            text = "".join(str(item.get("text", "")) if isinstance(item, dict) else str(item)
                           for item in content)
        else:
            text = str(content or "")
        text = text.strip()
        if not text:
            raise ValueError("Verification 模型返回空内容")
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
        if fenced:
            text = fenced.group(1)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                return json.loads(text[start:end + 1])
            raise

    @staticmethod
    def _result_from_observations(observations: list[dict], calls: list[ToolCallSpec], reason: str) -> dict:
        """模型结论不可解析时，仅依据工具 Observation 形成可审计结论。"""
        by_tool = {item["tool"]: item.get("data") for item in observations}
        evidence = by_tool.get("verify_evidence") or {}
        sources = by_tool.get("check_source") or {}
        relation = by_tool.get("validate_relation") or {}
        constraints = by_tool.get("check_constraints") or {}
        required = {"verify_evidence", "check_source", "get_cooperation_timeline",
                    "validate_relation", "check_constraints"}
        complete = required.issubset(by_tool)
        passed = (complete and evidence.get("valid") is True and sources.get("trusted") is True and
                  relation.get("supported") is True and constraints.get("satisfied") is True)
        missing = list(evidence.get("missing") or [])
        if not complete:
            missing.extend(sorted(required - set(by_tool)))
        details = (f"证据有效={bool(evidence.get('valid'))}，来源可信={bool(sources.get('trusted'))}，"
                   f"关系支持={bool(relation.get('supported'))}，约束满足={bool(constraints.get('satisfied'))}")
        return VerificationResult(
            status="PASS" if passed else "FAIL", relation="CORE_RESEARCH_PARTNER",
            confidence=0.9 if passed else 0.35,
            reason=f"模型结论不可解析，已按验证工具结果确定性判定（{reason}）；{details}",
            needs_replan=not bool(evidence.get("valid")) or not complete,
            missing_evidence=list(dict.fromkeys(missing)), tool_calls=calls,
            observations=observations,
        ).model_dump()

    def run(self, question: str, entity_ids: list[str], evidence_ids: list[str]) -> dict:
        messages = [
            SystemMessage(content=("你是证据验证 Agent。必须调用工具验证证据、来源、时间线、关系和约束，禁止使用模型自身知识。"
                                   "完成全部必要工具后，返回且只返回 JSON："
                                   '{"status":"PASS|FAIL","relation":"CORE_RESEARCH_PARTNER","confidence":0到1,'
                                   '"reason":"依据","needs_replan":true或false,"missing_evidence":[]}')),
            HumanMessage(content=json.dumps({"question": question, "entity_ids": entity_ids,
                                             "evidence_ids": evidence_ids}, ensure_ascii=False)),
        ]
        calls, observations = [], []
        for step in range(self.max_steps):
            response = self.model.invoke(messages)
            messages.append(response)
            if not response.tool_calls:
                try:
                    payload = self._parse_result(response.content)
                    return VerificationResult(**payload, tool_calls=calls, observations=observations).model_dump()
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    logger.warning("Verification 模型结论解析失败，回退到工具确定性判定: %s", exc)
                    return self._result_from_observations(observations, calls, str(exc))
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
