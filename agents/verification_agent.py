"""复杂语义 Verification Agent：复用通用 AgentHarness。"""

import json
import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage

from agents.harness import AgentHarness, HarnessConfig, HarnessMiddleware
from agents.verification_policies import VerificationPolicy, get_verification_policy
from models.llm import ModelFactory
from models.schemas import ToolCallSpec, VerificationResult
from services.telemetry import traced_span
from tools.provider import get_tools

logger = logging.getLogger(__name__)


class VerificationAgent:
    def __init__(
        self,
        max_steps: int = 8,
        middleware: list[HarnessMiddleware] | None = None,
        harness_config: HarnessConfig | None = None,
    ):
        tools = get_tools("verification")
        config = harness_config or HarnessConfig.from_env(
            "verification_agent",
            max_steps=max_steps,
            max_tool_calls=16,
        )
        self.harness = AgentHarness(
            "verification_agent",
            ModelFactory.verification_model(),
            tools,
            config=config,
            middleware=middleware,
        )
        # 兼容已有调试代码对 VerificationAgent.model/tools 的读取。
        self.model = self.harness.model
        self.tools = self.harness.tools
        self.max_steps = config.max_steps

    @staticmethod
    def _parse_result(content) -> dict:
        """兼容纯 JSON、Markdown JSON 代码块及 LangChain 内容块。"""
        if isinstance(content, dict):
            return content
        if isinstance(content, list):
            text = "".join(
                str(item.get("text", "")) if isinstance(item, dict) else str(item)
                for item in content
            )
        else:
            text = str(content or "")
        text = text.strip()
        if not text:
            raise ValueError("Verification 模型返回空内容")
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
        if fenced:
            text = fenced.group(1)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                return json.loads(text[start : end + 1])
            raise

    @staticmethod
    def _result_from_observations(
        observations: list[dict], calls: list[ToolCallSpec], reason: str,
        policy: VerificationPolicy,
    ) -> dict:
        """模型结论不可解析时，仅依据工具 Observation 形成可审计结论。"""
        by_tool = {item["tool"]: item.get("data") for item in observations}
        evidence = by_tool.get("verify_evidence") or {}
        sources = by_tool.get("check_source") or {}
        relation = by_tool.get("validate_relation") or {}
        constraints = by_tool.get("check_constraints") or {}
        required = set(policy.tool_sequence)
        complete = required.issubset(by_tool)
        relation_ok = (
            bool(relation.get("supported"))
            if "validate_relation" in required else True
        )
        constraints_ok = (
            bool(constraints.get("satisfied"))
            if "check_constraints" in required else True
        )
        passed = (
            complete
            and evidence.get("valid") is True
            and sources.get("trusted") is True
            and relation_ok
            and constraints_ok
        )
        missing = list(evidence.get("missing") or [])
        if not complete:
            missing.extend(sorted(required - set(by_tool)))
        details = (
            f"证据有效={bool(evidence.get('valid'))}，来源可信={bool(sources.get('trusted'))}，"
            f"关系支持={bool(relation.get('supported'))}，约束满足={bool(constraints.get('satisfied'))}"
        )
        return VerificationResult(
            status="PASS" if passed else "FAIL",
            claim_type=policy.claim_type,
            relation=policy.relation,
            confidence=0.9 if passed else 0.35,
            reason=f"模型结论不可解析，已按验证工具结果确定性判定（{reason}）；{details}",
            needs_replan=not bool(evidence.get("valid")) or not complete,
            missing_evidence=list(dict.fromkeys(missing)),
            tool_calls=calls,
            observations=observations,
        ).model_dump()

    def run(
        self,
        question: str,
        entity_ids: list[str],
        evidence_ids: list[str],
        evidence_records: list[dict] | None = None,
        claim_type: str | None = None,
        thread_id: str | None = None,
    ) -> dict:
        with traced_span(
            "agent.verification_agent",
            "agent",
            {
                "agent.name": "verification_agent",
                "agent.entity_count": len(entity_ids),
                "agent.evidence_count": len(evidence_ids),
            },
        ) as span:
            result = self._run_impl(
                question, entity_ids, evidence_ids, evidence_records or [],
                claim_type, thread_id,
            )
            span.set_attribute(
                "agent.tool_call_count", len(result.get("tool_calls", []))
            )
            return result

    def _run_impl(
        self,
        question: str,
        entity_ids: list[str],
        evidence_ids: list[str],
        evidence_records: list[dict],
        claim_type: str | None,
        thread_id: str | None = None,
    ) -> dict:
        policy = get_verification_policy(claim_type, question)
        messages = [
            SystemMessage(
                content=(
                    "你是证据验证 Agent。严格按照 verification_plan 调用其中列出的工具，禁止使用模型自身知识。"
                    "完成全部必要工具后，返回且只返回 JSON："
                    '{"status":"PASS|FAIL","claim_type":"结论类型","relation":"关系","confidence":0到1,'
                    '"reason":"依据","needs_replan":true或false,"missing_evidence":[]}'
                )
            ),
            HumanMessage(
                content=json.dumps(
                    {
                        "question": question,
                        "entity_ids": entity_ids,
                        "evidence_ids": evidence_ids,
                        "evidence_records": evidence_records,
                        "verification_plan": policy.as_dict(),
                    },
                    ensure_ascii=False,
                )
            ),
        ]
        run = self.harness.execute(messages, thread_id)
        if run.final_response is not None:
            try:
                payload = self._parse_result(run.final_response)
                return VerificationResult(
                    **({
                        **payload,
                        "claim_type": payload.get("claim_type", policy.claim_type),
                        "relation": payload.get("relation", policy.relation),
                    }),
                    tool_calls=run.tool_calls, observations=run.observations
                ).model_dump()
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                logger.warning(
                    "Verification 模型结论解析失败，回退到工具确定性判定: %s", exc
                )
                return self._result_from_observations(
                    run.observations, run.tool_calls, str(exc), policy
                )
        reason = "; ".join(run.errors) or run.stop_reason
        return self._result_from_observations(
            run.observations, run.tool_calls, reason, policy
        )


def build_verification_agent() -> VerificationAgent:
    return VerificationAgent()
