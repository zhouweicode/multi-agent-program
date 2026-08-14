"""统一模型入口。默认 Mock 模型以真实 tool_call 协议驱动工具，可替换为任意 ChatModel。"""
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any
from langchain_core.messages import AIMessage, ToolMessage
from models.schemas import RouterOutput, SupervisorPlan, PlannedTask


class MockStructuredModel:
    """离线教学模型：返回 Pydantic，而不是散乱文本。"""
    def invoke_router(self, question: str) -> RouterOutput:
        mentions = list(dict.fromkeys(re.findall(r"张伟|李明", question)))
        domain_hits = {
            "achievement": any(x in question for x in ("论文", "科研", "学术", "专利")),
            "talent": any(x in question for x in ("职业", "任职", "同事", "校友")),
            "enterprise": any(x in question for x in ("企业", "公司", "顾问")),
            "industry": any(x in question for x in ("产业链", "产业事件", "TOP", "节点")),
            "graph": any(x in question for x in ("间接关系", "多跳", "路径", "邻居", "关系强度")),
        }
        matched = [name for name, hit in domain_hits.items() if hit]
        complex_query = "综合" in question or len(matched) > 1
        domain = matched[0] if matched else "talent"
        return RouterOutput(intent="跨领域综合分析" if complex_query else "事实查询", entity_mentions=mentions,
                            complexity="complex" if complex_query else "simple", primary_domain=domain,
                            requires_verification="长期稳定" in question or "核心科研合作伙伴" in question)

    def invoke_supervisor(self, question: str, resolved_entities: dict[str, str], validation_result: dict | None = None) -> SupervisorPlan:
        specs = [
            ("talent", "talent_agent", "查询专家的共同任职经历与职业关系", ("职业", "任职", "同事", "校友")),
            ("achievement", "achievement_agent", "查询专家的共同论文与学术合作", ("学术", "论文", "科研", "专利")),
            ("enterprise", "enterprise_agent", "查询专家与企业的角色、项目和专利关系", ("企业", "公司", "顾问")),
            ("industry", "industry_agent", "查询产业链结构、企业和重点事件", ("产业", "产业链", "事件", "TOP")),
            ("graph", "graph_reasoning_agent", "查询实体间路径、多跳关系和关系强度", ("间接", "多跳", "路径", "关系强度", "所有可能")),
        ]
        tasks = [PlannedTask(task_id=f"task_{domain}", agent=agent, goal=goal)
                 for domain, agent, goal, keywords in specs if any(word in question for word in keywords)]
        if not tasks:
            tasks = [PlannedTask(task_id="task_achievement", agent="achievement_agent", goal="查询专家科研合作")]
        return SupervisorPlan(tasks=tasks, execution_mode="parallel", reason=f"问题涉及 {len(tasks)} 个业务领域，需要并行查询后合并")


@dataclass
class MockToolCallingModel:
    """生成 LangChain AIMessage.tool_calls；Agent 据此执行已绑定的领域工具。"""
    domain: str

    def bind_tools(self, tools: list[Any]) -> "MockToolCallingModel":
        self.allowed_tools = {t.name for t in tools}
        return self

    def invoke(self, payload: dict[str, Any]) -> AIMessage:
        entity_ids = list(payload["resolved_entities"].values())
        calls = []
        call_specs = {
            "talent": [("match_employment_overlap", {"entity_ids": entity_ids})],
            "achievement": [("get_common_papers", {"entity_ids": entity_ids}), ("get_common_projects", {"entity_ids": entity_ids}),
                            ("aggregate_cooperation", {"entity_ids": entity_ids})],
            "enterprise": [("get_person_company_roles", {"entity_ids": entity_ids}), ("get_company_projects", {"company_id": "company_001"}), ("get_company_patents", {"company_id": "company_001"})],
            "industry": [("get_chain_structure", {"chain_id": "chain_ai"}), ("get_node_companies", {"node_id": "node_model"}),
                         ("get_node_events", {"node_id": "node_model"}), ("rank_top_events", {"node_id": "node_model", "top_n": 2})],
            "graph": [("get_neighbors", {"entity_id": entity_ids[0] if entity_ids else "person_zw_001"}),
                      ("find_path", {"source_id": entity_ids[0], "target_id": entity_ids[1]}) if len(entity_ids) >= 2 else ("k_hop_expand", {"entity_id": entity_ids[0] if entity_ids else "person_zw_001", "k": 2}),
                      ("calculate_path_strength", {"source_id": entity_ids[0], "target_id": entity_ids[1]}) if len(entity_ids) >= 2 else ("k_hop_expand", {"entity_id": entity_ids[0] if entity_ids else "person_zw_001", "k": 2})],
        }
        for name, args in call_specs[self.domain]:
            if name in self.allowed_tools:
                calls.append({"name": name, "args": args, "id": str(uuid.uuid4()), "type": "tool_call"})
        return AIMessage(content=json.dumps({"reason": payload["goal"]}, ensure_ascii=False), tool_calls=calls)


@dataclass
class MockVerificationModel:
    """按 Observation 逐步决策的离线验证模型，使用标准 Messages/Tool Call 协议。"""
    def bind_tools(self, tools: list[Any]) -> "MockVerificationModel":
        self.allowed_tools = {tool.name for tool in tools}
        return self

    def invoke(self, messages: list[Any]) -> AIMessage:
        context = json.loads(messages[1].content)
        observations = [json.loads(msg.content) for msg in messages if isinstance(msg, ToolMessage)]
        sequence = [
            ("verify_evidence", {"evidence_ids": context["evidence_ids"]}),
            ("check_source", {"evidence_ids": context["evidence_ids"]}),
            ("get_cooperation_timeline", {"entity_ids": context["entity_ids"]}),
            ("validate_relation", {"entity_ids": context["entity_ids"], "relation": "CORE_RESEARCH_PARTNER"}),
            ("check_constraints", {"timeline": observations[2] if len(observations) > 2 else [],
                                   "min_year_span": 2, "min_achievements": 3}),
        ]
        if len(observations) < len(sequence):
            name, args = sequence[len(observations)]
            if name in self.allowed_tools:
                return AIMessage(content=f"验证步骤 {len(observations) + 1}", tool_calls=[
                    {"name": name, "args": args, "id": str(uuid.uuid4()), "type": "tool_call"}
                ])
        evidence_ok = bool(observations) and observations[0].get("valid", False)
        sources_ok = len(observations) > 1 and observations[1].get("trusted", False)
        relation_ok = len(observations) > 3 and observations[3].get("supported", False)
        constraints_ok = len(observations) > 4 and observations[4].get("satisfied", False)
        passed = evidence_ok and sources_ok and relation_ok and constraints_ok
        missing = [] if evidence_ok else ["有效科研合作证据"]
        result = {"status": "PASS" if passed else "FAIL", "relation": "CORE_RESEARCH_PARTNER",
                  "confidence": 0.92 if passed else 0.35,
                  "reason": "共同论文和项目覆盖多个年份，证据来源可信且满足长期稳定合作约束" if passed else "现有证据未满足长期稳定核心合作约束",
                  "needs_replan": not evidence_ok, "missing_evidence": missing}
        return AIMessage(content=json.dumps(result, ensure_ascii=False))


class ModelFactory:
    """业务代码只依赖此工厂；后续可按配置返回真实 OpenAI/私有模型。"""
    @staticmethod
    def structured_model() -> MockStructuredModel:
        return MockStructuredModel()

    @staticmethod
    def tool_calling_model(domain: str) -> MockToolCallingModel:
        return MockToolCallingModel(domain)

    @staticmethod
    def verification_model() -> MockVerificationModel:
        return MockVerificationModel()
