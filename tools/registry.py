"""统一注册全部领域 Tool、Agent 契约和 Skill Capability。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from tools.achievement_tools import (
    aggregate_cooperation,
    get_author_papers,
    get_common_papers,
    get_common_patents,
    get_common_projects,
    get_person_patents,
)
from tools.contracts import AgentContract, CapabilitySpec, ToolSpec
from tools.enterprise_tools import (
    get_company_patents,
    get_company_projects,
    get_person_company_roles,
)
from tools.graph_tools import (
    aggregate_graph,
    calculate_path_strength,
    find_path,
    find_paths,
    get_graph_schema,
    get_neighbors,
    get_neighbors_filtered,
    k_hop_expand,
    query_subgraph,
)
from tools.industry_tools import (
    get_chain_structure,
    get_node_companies,
    get_node_events,
    rank_top_events,
    search_industry_segments,
)
from tools.talent_tools import (
    get_education_history,
    get_employment_history,
    get_person_profile,
    match_employment_overlap,
)
from tools.verification_tools import (
    check_constraints,
    check_source,
    get_cooperation_timeline,
    validate_relation,
    verify_evidence,
)
from tools.web_search_tools import search_web


class ToolRegistry:
    """不可变语义的注册中心；运行时只选择实现，不改写授权契约。"""

    def __init__(
        self,
        tools: Iterable[ToolSpec],
        agents: Iterable[AgentContract],
        capabilities: Iterable[CapabilitySpec],
    ) -> None:
        self._tools = self._unique("Tool", tools, lambda item: item.name)
        self._agents = self._unique("Agent", agents, lambda item: item.agent)
        self._capabilities = self._unique(
            "Capability", capabilities, lambda item: item.name
        )
        self._tools_by_domain: dict[str, tuple[ToolSpec, ...]] = defaultdict(tuple)
        grouped: dict[str, list[ToolSpec]] = defaultdict(list)
        for spec in self._tools.values():
            grouped[spec.domain].append(spec)
        self._tools_by_domain = {
            domain: tuple(items) for domain, items in grouped.items()
        }
        self._validate()

    @staticmethod
    def _unique(label: str, values: Iterable[Any], key) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for item in values:
            item_key = key(item)
            if item_key in result:
                raise ValueError(f"重复 {label}: {item_key}")
            result[item_key] = item
        return result

    def _validate(self) -> None:
        fact_to_tool: dict[str, str] = {}
        for spec in self._tools.values():
            agent = self._agents.get(spec.agent)
            if agent is None or agent.domain != spec.domain:
                raise ValueError(
                    f"Tool {spec.name} 的 Agent/Domain 契约不一致: "
                    f"{spec.agent}/{spec.domain}"
                )
            for fact_type in spec.fact_types:
                previous = fact_to_tool.setdefault(fact_type, spec.name)
                if previous != spec.name:
                    raise ValueError(
                        f"FactType {fact_type} 同时映射到 {previous} 和 {spec.name}"
                    )
        for agent in self._agents.values():
            for fact_type in agent.default_required_fact_types:
                if fact_type not in fact_to_tool:
                    raise ValueError(
                        f"Agent {agent.agent} 引用了未知 FactType: {fact_type}"
                    )
        for capability in self._capabilities.values():
            agent = self._agents.get(capability.agent)
            if agent is None or agent.domain != capability.domain:
                raise ValueError(
                    f"Capability {capability.name} 的 Agent/Domain 契约不一致"
                )
            for fact_type in capability.required_fact_types:
                tool_name = fact_to_tool.get(fact_type)
                if tool_name is None:
                    raise ValueError(
                        f"Capability {capability.name} 引用了未知 FactType: {fact_type}"
                    )
                if self._tools[tool_name].agent != capability.agent:
                    raise ValueError(
                        f"Capability {capability.name} 的 FactType {fact_type} "
                        f"不属于 {capability.agent}"
                    )

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ValueError(f"未知 Tool: {name}") from exc

    def get_agent(self, agent: str) -> AgentContract:
        try:
            return self._agents[agent]
        except KeyError as exc:
            raise ValueError(f"未知 Agent: {agent}") from exc

    def get_capability(self, name: str) -> CapabilitySpec:
        try:
            return self._capabilities[name]
        except KeyError as exc:
            raise ValueError(f"未知 Capability: {name}") from exc

    def list(self, domain: str | None = None) -> list[ToolSpec]:
        return list(
            self._tools.values()
            if domain is None
            else self._tools_by_domain.get(domain, ())
        )

    def list_capabilities(self) -> list[CapabilitySpec]:
        return list(self._capabilities.values())

    def tool_names(self, domain: str) -> list[str]:
        if domain not in self._tools_by_domain:
            raise ValueError(f"未知工具组: {domain}")
        return [item.name for item in self._tools_by_domain[domain]]

    def local_tools(self, domain: str) -> list[Any]:
        """返回带统一元数据的本地 Tool 副本，避免修改模块级单例。"""
        result = []
        for spec in self.list(domain):
            metadata = dict(getattr(spec.implementation, "metadata", None) or {})
            metadata.update(self.metadata(spec.name, transport="local"))
            copier = getattr(spec.implementation, "model_copy", None)
            result.append(
                copier(update={"metadata": metadata})
                if callable(copier)
                else spec.implementation
            )
        return result

    def metadata(self, name: str, *, transport: str) -> dict[str, Any]:
        spec = self.get(name)
        return {
            "canonical_tool_name": spec.name,
            "tool_domain": spec.domain,
            "authorized_agent": spec.agent,
            "fact_types": list(spec.fact_types),
            "capabilities": list(spec.capabilities),
            "trust_level": spec.trust_level,
            "tool_transport": transport,
            "tool_source": "local:repository" if transport == "local" else "mcp",
            "idempotent": spec.idempotent,
            "open_world": spec.open_world,
        }

    @property
    def agent_domains(self) -> dict[str, str]:
        return {name: spec.domain for name, spec in self._agents.items()}

    @property
    def fact_type_to_tool(self) -> dict[str, str]:
        return {
            fact_type: spec.name
            for spec in self._tools.values()
            for fact_type in spec.fact_types
        }

    @property
    def default_required_fact_types(self) -> dict[str, list[str]]:
        return {
            name: list(spec.default_required_fact_types)
            for name, spec in self._agents.items()
        }


AGENT_CONTRACTS = (
    AgentContract("talent_agent", "talent", ("employment_overlap",)),
    AgentContract(
        "achievement_agent",
        "achievement",
        ("common_papers", "common_projects", "cooperation_summary"),
    ),
    AgentContract(
        "enterprise_agent",
        "enterprise",
        ("company_roles", "company_projects", "company_patents"),
    ),
    AgentContract(
        "industry_agent",
        "industry",
        ("chain_structure", "node_companies", "node_events", "ranked_events"),
    ),
    AgentContract(
        "graph_reasoning_agent",
        "graph",
        ("neighbors", "path", "path_strength"),
    ),
    AgentContract("web_research_agent", "web", ("web_sources",)),
    AgentContract(
        "verification_agent",
        "verification",
        (
            "evidence_verification",
            "source_check",
            "cooperation_timeline",
            "relation_validation",
            "constraint_check",
        ),
    ),
)


CAPABILITY_SPECS = (
    CapabilitySpec(
        "expert_profile_history",
        "talent_agent",
        "talent",
        "为专家报告查询单个专家的基础画像、教育经历和任职经历",
        ("person_profile", "education", "employment"),
    ),
    CapabilitySpec(
        "research_achievements",
        "achievement_agent",
        "achievement",
        "为专家报告查询单个专家的论文和专利成果",
        ("papers", "patents"),
    ),
    CapabilitySpec(
        "enterprise_relations",
        "enterprise_agent",
        "enterprise",
        "为专家报告查询专家的企业角色，以及与其相关的企业项目和企业专利",
        ("company_roles", "company_projects", "company_patents"),
    ),
    CapabilitySpec(
        "cooperation_network",
        "graph_reasoning_agent",
        "graph",
        "为专家报告查询专家的一跳合作与关联网络，并进行有限的局部子图扩展",
        ("neighbors",),
    ),
    CapabilitySpec(
        "external_public_evidence",
        "web_research_agent",
        "web",
        "为专家报告搜索与目标专家直接相关的公开网页候选证据",
        ("web_sources",),
    ),
    CapabilitySpec(
        "industry_landscape_core",
        "industry_agent",
        "industry",
        "为产业全景报告检索产业节点，并查询产业链结构、关联企业和重点产业事件",
        ("industry_segments", "chain_structure", "node_companies", "ranked_events"),
    ),
    CapabilitySpec(
        "external_industry_evidence",
        "web_research_agent",
        "web",
        "为产业全景报告搜索与目标产业直接相关的公开网页候选证据",
        ("web_sources",),
    ),
    CapabilitySpec(
        "graph_filtered_traversal",
        "graph_reasoning_agent",
        "graph",
        "按关系、方向、时间、权重和属性约束精确查询实体邻居",
        ("filtered_neighbors",),
    ),
    CapabilitySpec(
        "graph_path_analysis",
        "graph_reasoning_agent",
        "graph",
        "查询并比较实体间 Top-K 最短或高权重路径",
        ("ranked_paths",),
    ),
    CapabilitySpec(
        "graph_subgraph_analysis",
        "graph_reasoning_agent",
        "graph",
        "围绕种子实体取得规模受限的局部子图",
        ("bounded_subgraph",),
    ),
    CapabilitySpec(
        "graph_aggregation",
        "graph_reasoning_agent",
        "graph",
        "在治理 Schema 内执行图统计、分组、去重和排序",
        ("graph_aggregation",),
    ),
    CapabilitySpec(
        "graph_schema_discovery",
        "graph_reasoning_agent",
        "graph",
        "读取允许 Planner 使用的图 Label、关系、属性和查询上限",
        ("graph_schema",),
    ),
)


TOOL_SPECS = (
    ToolSpec(
        "get_person_profile",
        "talent",
        "talent_agent",
        get_person_profile,
        ("person_profile",),
        ("expert_profile_history",),
    ),
    ToolSpec(
        "get_employment_history",
        "talent",
        "talent_agent",
        get_employment_history,
        ("employment",),
        ("expert_profile_history",),
    ),
    ToolSpec(
        "get_education_history",
        "talent",
        "talent_agent",
        get_education_history,
        ("education",),
        ("expert_profile_history",),
    ),
    ToolSpec(
        "match_employment_overlap",
        "talent",
        "talent_agent",
        match_employment_overlap,
        ("employment_overlap",),
    ),
    ToolSpec(
        "get_author_papers",
        "achievement",
        "achievement_agent",
        get_author_papers,
        ("papers",),
        ("research_achievements",),
    ),
    ToolSpec(
        "get_common_papers",
        "achievement",
        "achievement_agent",
        get_common_papers,
        ("common_papers",),
    ),
    ToolSpec(
        "aggregate_cooperation",
        "achievement",
        "achievement_agent",
        aggregate_cooperation,
        ("cooperation_summary",),
    ),
    ToolSpec(
        "get_common_projects",
        "achievement",
        "achievement_agent",
        get_common_projects,
        ("common_projects",),
    ),
    ToolSpec(
        "get_person_patents",
        "achievement",
        "achievement_agent",
        get_person_patents,
        ("patents",),
        ("research_achievements",),
    ),
    ToolSpec(
        "get_common_patents",
        "achievement",
        "achievement_agent",
        get_common_patents,
        ("common_patents",),
    ),
    ToolSpec(
        "get_person_company_roles",
        "enterprise",
        "enterprise_agent",
        get_person_company_roles,
        ("company_roles",),
        ("enterprise_relations",),
    ),
    ToolSpec(
        "get_company_projects",
        "enterprise",
        "enterprise_agent",
        get_company_projects,
        ("company_projects",),
        ("enterprise_relations",),
    ),
    ToolSpec(
        "get_company_patents",
        "enterprise",
        "enterprise_agent",
        get_company_patents,
        ("company_patents",),
        ("enterprise_relations",),
    ),
    ToolSpec(
        "search_industry_segments",
        "industry",
        "industry_agent",
        search_industry_segments,
        ("industry_segments",),
        ("industry_landscape_core",),
    ),
    ToolSpec(
        "get_chain_structure",
        "industry",
        "industry_agent",
        get_chain_structure,
        ("chain_structure",),
        ("industry_landscape_core",),
    ),
    ToolSpec(
        "get_node_companies",
        "industry",
        "industry_agent",
        get_node_companies,
        ("node_companies",),
        ("industry_landscape_core",),
    ),
    ToolSpec(
        "get_node_events",
        "industry",
        "industry_agent",
        get_node_events,
        ("node_events",),
    ),
    ToolSpec(
        "rank_top_events",
        "industry",
        "industry_agent",
        rank_top_events,
        ("ranked_events",),
        ("industry_landscape_core",),
    ),
    ToolSpec(
        "get_neighbors",
        "graph",
        "graph_reasoning_agent",
        get_neighbors,
        ("neighbors",),
        ("cooperation_network",),
    ),
    ToolSpec("find_path", "graph", "graph_reasoning_agent", find_path, ("path",)),
    ToolSpec(
        "k_hop_expand",
        "graph",
        "graph_reasoning_agent",
        k_hop_expand,
        ("k_hop_subgraph",),
    ),
    ToolSpec(
        "calculate_path_strength",
        "graph",
        "graph_reasoning_agent",
        calculate_path_strength,
        ("path_strength",),
    ),
    ToolSpec(
        "get_neighbors_filtered",
        "graph",
        "graph_reasoning_agent",
        get_neighbors_filtered,
        ("filtered_neighbors",),
        ("graph_filtered_traversal",),
    ),
    ToolSpec(
        "find_paths",
        "graph",
        "graph_reasoning_agent",
        find_paths,
        ("ranked_paths",),
        ("graph_path_analysis",),
    ),
    ToolSpec(
        "query_subgraph",
        "graph",
        "graph_reasoning_agent",
        query_subgraph,
        ("bounded_subgraph",),
        ("graph_subgraph_analysis",),
    ),
    ToolSpec(
        "aggregate_graph",
        "graph",
        "graph_reasoning_agent",
        aggregate_graph,
        ("graph_aggregation",),
        ("graph_aggregation",),
    ),
    ToolSpec(
        "get_graph_schema",
        "graph",
        "graph_reasoning_agent",
        get_graph_schema,
        ("graph_schema",),
        ("graph_schema_discovery",),
    ),
    ToolSpec(
        "verify_evidence",
        "verification",
        "verification_agent",
        verify_evidence,
        ("evidence_verification",),
    ),
    ToolSpec(
        "check_source",
        "verification",
        "verification_agent",
        check_source,
        ("source_check",),
    ),
    ToolSpec(
        "get_cooperation_timeline",
        "verification",
        "verification_agent",
        get_cooperation_timeline,
        ("cooperation_timeline",),
    ),
    ToolSpec(
        "validate_relation",
        "verification",
        "verification_agent",
        validate_relation,
        ("relation_validation",),
    ),
    ToolSpec(
        "check_constraints",
        "verification",
        "verification_agent",
        check_constraints,
        ("constraint_check",),
    ),
    ToolSpec(
        "search_web",
        "web",
        "web_research_agent",
        search_web,
        ("web_sources",),
        ("external_public_evidence", "external_industry_evidence"),
        trust_level="remote_content",
        open_world=True,
    ),
)


tool_registry = ToolRegistry(TOOL_SPECS, AGENT_CONTRACTS, CAPABILITY_SPECS)
