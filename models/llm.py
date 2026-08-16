"""统一模型入口。默认 Mock 模型以真实 tool_call 协议驱动工具，可替换为任意 ChatModel。"""
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from models.schemas import RouterOutput, SupervisorPlan, PlannedTask
from models.settings import Settings
from models.contracts import DEFAULT_REQUIRED_FACT_TYPES


class MockStructuredModel:
    """离线教学模型：返回 Pydantic，而不是散乱文本。"""
    def invoke_router(self, question: str) -> RouterOutput:
        mentions = list(dict.fromkeys(re.findall(r"张伟|李明", question)))
        domain_hits = {
            "achievement": any(x in question for x in ("论文", "科研", "学术", "专利")),
            "talent": any(x in question for x in ("职业", "任职", "工作", "单位", "同事", "校友")),
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

    def invoke_supervisor(self, question: str, resolved_entities: dict[str, str], validation_result: dict | None = None,
                          verification_result: dict | None = None, task_history: list[dict] | None = None) -> SupervisorPlan:
        specs = [
            ("talent", "talent_agent", "查询专家的共同任职经历与职业关系", ("职业", "任职", "同事", "校友")),
            ("achievement", "achievement_agent", "查询专家的共同论文与学术合作", ("学术", "论文", "科研", "专利")),
            ("enterprise", "enterprise_agent", "查询专家与企业的角色、项目和专利关系", ("企业", "公司", "顾问")),
            ("industry", "industry_agent", "查询产业链结构、企业和重点事件", ("产业", "产业链", "事件", "TOP")),
            ("graph", "graph_reasoning_agent", "查询实体间路径、多跳关系和关系强度", ("间接", "多跳", "路径", "关系强度", "所有可能")),
        ]
        missing_domains = set((validation_result or {}).get("missing_domains", []))
        missing_evidence = (verification_result or {}).get("missing_evidence", [])
        is_replan = bool(validation_result or verification_result)
        tasks = [PlannedTask(task_id=f"{'replan' if is_replan else 'task'}_{domain}", agent=agent,
                             goal=(goal + (f"；重点补充：{'、'.join(missing_evidence)}" if missing_evidence else "")),
                             required_fact_types=DEFAULT_REQUIRED_FACT_TYPES[agent],
                             required_entity_ids=list(resolved_entities.values()))
                 for domain, agent, goal, keywords in specs
                 if (domain in missing_domains or (missing_evidence and domain == "achievement") or
                     (not is_replan and any(word in question for word in keywords)))]
        if not tasks:
            tasks = [PlannedTask(task_id="task_achievement", agent="achievement_agent", goal="查询专家科研合作",
                                 required_fact_types=DEFAULT_REQUIRED_FACT_TYPES["achievement_agent"],
                                 required_entity_ids=list(resolved_entities.values()))]
        reason = "根据缺失领域或证据执行最小化重规划" if is_replan else f"问题涉及 {len(tasks)} 个业务领域，需要并行查询后合并"
        return SupervisorPlan(tasks=tasks, execution_mode="parallel", reason=reason)


@dataclass
class MockToolCallingModel:
    """生成 LangChain AIMessage.tool_calls；Agent 据此执行已绑定的领域工具。"""
    domain: str

    def bind_tools(self, tools: list[Any]) -> "MockToolCallingModel":
        self.allowed_tools = {t.name for t in tools}
        return self

    def invoke(self, messages: list[Any]) -> AIMessage:
        payload = json.loads(next(msg.content for msg in messages if isinstance(msg, HumanMessage)))
        goal = payload["goal"]
        entity_ids = list(payload["resolved_entities"].values())
        # Mock 模型也必须模拟真实模型的“按任务选择工具”，不能只按领域硬编码。
        # 单实体查询使用画像/履历工具；只有双实体关系查询才计算任职重叠。
        if self.domain == "talent":
            if len(entity_ids) == 1:
                talent_specs = ([('get_education_history', {"entity_id": entity_ids[0]})]
                                if any(word in goal for word in ("教育", "学历", "毕业")) else [
                                    ("get_person_profile", {"entity_id": entity_ids[0]}),
                                    ("get_employment_history", {"entity_id": entity_ids[0]}),
                                ])
            else:
                talent_specs = [("match_employment_overlap", {"entity_ids": entity_ids})]
        else:
            talent_specs = []
        if self.domain == "achievement" and "专利" in goal:
            achievement_specs = ([('get_person_patents', {"entity_id": entity_ids[0]})] if len(entity_ids) == 1 else
                                 [('get_common_patents', {"entity_ids": entity_ids})])
        elif self.domain == "achievement" and len(entity_ids) == 1:
            achievement_specs = [("get_author_papers", {"entity_id": entity_ids[0]})]
        else:
            achievement_specs = [
                ("get_common_papers", {"entity_ids": entity_ids}),
                ("get_common_projects", {"entity_ids": entity_ids}),
                ("aggregate_cooperation", {"entity_ids": entity_ids}),
            ]
        call_specs = {
            "talent": talent_specs,
            "achievement": achievement_specs,
            "enterprise": [("get_person_company_roles", {"entity_ids": entity_ids}), ("get_company_projects", {"company_id": "company_001"}), ("get_company_patents", {"company_id": "company_001"})],
            "industry": [("get_chain_structure", {"chain_id": "chain_ai"}), ("get_node_companies", {"node_id": "node_model"}),
                         ("get_node_events", {"node_id": "node_model"}), ("rank_top_events", {"node_id": "node_model", "top_n": 2})],
            "graph": [("get_neighbors", {"entity_id": entity_ids[0] if entity_ids else "person_zw_001"}),
                      ("find_path", {"source_id": entity_ids[0], "target_id": entity_ids[1]}) if len(entity_ids) >= 2 else ("k_hop_expand", {"entity_id": entity_ids[0] if entity_ids else "person_zw_001", "k": 2}),
                      ("calculate_path_strength", {"source_id": entity_ids[0], "target_id": entity_ids[1]}) if len(entity_ids) >= 2 else ("k_hop_expand", {"entity_id": entity_ids[0] if entity_ids else "person_zw_001", "k": 2})],
        }
        completed = len([msg for msg in messages if isinstance(msg, ToolMessage)])
        specs = [(name, args) for name, args in call_specs[self.domain] if name in self.allowed_tools]
        if completed < len(specs):
            name, args = specs[completed]
            return AIMessage(content=f"执行领域任务步骤 {completed + 1}", tool_calls=[
                {"name": name, "args": args, "id": str(uuid.uuid4()), "type": "tool_call"}
            ])
        return AIMessage(content=json.dumps({"status": "complete", "goal": goal}, ensure_ascii=False))


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
            ("verify_evidence", {"evidence_ids": context["evidence_ids"], "entity_ids": context["entity_ids"]}),
            ("check_source", {"evidence_ids": context["evidence_ids"], "entity_ids": context["entity_ids"]}),
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
    """统一模型工厂；默认 Mock，MODEL_PROVIDER=openai 时启用真实模型。"""
    @staticmethod
    def _chat_model() -> Any:
        settings = Settings.from_env()
        if settings.model_provider != "openai":
            raise ValueError(f"不支持的 MODEL_PROVIDER: {settings.model_provider}")
        if not settings.model_api_key:
            raise ValueError("MODEL_PROVIDER=openai 时必须设置 MODEL_API_KEY 或 OPENAI_API_KEY")
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=settings.model_name, api_key=settings.model_api_key,
                          base_url=settings.model_base_url, temperature=settings.model_temperature,
                          timeout=settings.model_request_timeout, max_retries=settings.model_max_retries)

    @staticmethod
    def structured_model() -> Any:
        if Settings.from_env().model_provider == "mock":
            return MockStructuredModel()
        return OpenAIStructuredModel(ModelFactory._chat_model())

    @staticmethod
    def tool_calling_model(domain: str) -> Any:
        if Settings.from_env().model_provider == "mock":
            return MockToolCallingModel(domain)
        return ModelFactory._chat_model()

    @staticmethod
    def verification_model() -> Any:
        if Settings.from_env().model_provider == "mock":
            return MockVerificationModel()
        return ModelFactory._chat_model()


class OpenAIStructuredModel:
    """为 Router/Supervisor 提供与 MockStructuredModel 相同的业务接口。"""
    def __init__(self, chat_model: Any):
        self.chat_model = chat_model

    def _invoke_json(self, schema: type[Any], instruction: str, payload: dict[str, Any]) -> Any:
        """使用 OpenAI 兼容接口普遍支持的 JSON Mode。

        GLM-5.2 当前不会稳定遵循 LangChain 默认的 native structured output，
        因此显式给出 JSON Schema，并要求只返回 JSON 对象，再交给 Pydantic 校验。
        """
        model = self.chat_model.with_structured_output(schema, method="json_mode")
        prompt = (
            f"{instruction}\n"
            "你必须只返回一个合法 JSON 对象，不要输出 Markdown、解释或代码块。\n"
            f"JSON Schema：{json.dumps(schema.model_json_schema(), ensure_ascii=False)}\n"
            f"输入：{json.dumps(payload, ensure_ascii=False)}"
        )
        return model.invoke(prompt)

    def invoke_router(self, question: str) -> RouterOutput:
        return self._invoke_json(
            RouterOutput,
            """你是 GraphRAG Router，只做意图分类、实体 mention 提取、复杂度判断和主领域分类。
领域边界必须严格遵守：
- talent：专家画像、在哪里工作、任职经历、教育经历、同事关系、校友关系；
- achievement：发表论文、共同论文、专利、科研项目、科研成果和学术合作；
- enterprise：专家与企业的任职、顾问、企业项目、企业专利和技术合作；
- industry：产业链结构、产业节点、产业企业、产业产品和产业事件；
- graph：一跳邻居、多跳路径、间接关系、局部子图和关系强度。
示例：“张伟发表过哪些论文？”必须归为 achievement；“张伟在哪里工作？”必须归为 talent。
只涉及一个领域时 complexity=simple；需要多个领域协作时 complexity=complex。""",
            {"question": question},
        )

    def invoke_supervisor(self, question: str, resolved_entities: dict[str, str], validation_result: dict | None = None,
                          verification_result: dict | None = None, task_history: list[dict] | None = None) -> SupervisorPlan:
        context = {"question": question, "resolved_entities": resolved_entities, "validation_result": validation_result,
                   "verification_result": verification_result, "task_history": task_history or []}
        return self._invoke_json(
            SupervisorPlan,
            """你是 Planner Node，不调用业务工具。只拆解需要补充的领域任务；重规划时只返回缺失领域。
领域边界：职业/任职/同事归 talent_agent；论文/科研项目/学术合作归 achievement_agent；
企业角色/企业项目/企业专利/产业合作归 enterprise_agent；产业链结构/产业节点/产业事件归 industry_agent；
只有明确询问路径、多跳、间接关系、邻居或关系强度时才调用 graph_reasoning_agent。
“综合分析两人的学术、职业和产业合作关系”应并行调用 talent_agent、achievement_agent、enterprise_agent，
不能因为出现“关系”二字就调用 graph_reasoning_agent。required_fact_types 表示任务必须返回的业务事实类型，
required_entity_ids 必须填写本次输入中的 canonical entity ID；Node 会用确定性领域契约再次规范化。""",
            context,
        )
