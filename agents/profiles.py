"""Declarative domain profiles used by every specialist Agent."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentProfile:
    name: str
    title: str
    responsibilities: tuple[str, ...]
    tool_policy: tuple[str, ...]
    forbidden_claims: tuple[str, ...]

    def render(self) -> str:
        responsibilities = "；".join(self.responsibilities)
        tool_policy = "；".join(self.tool_policy)
        forbidden = "；".join(self.forbidden_claims)
        return (
            f"角色：{self.title}。职责：{responsibilities}。"
            f"工具策略：{tool_policy}。禁止事项：{forbidden}。"
        )


PROFILES = {
    "talent_agent": AgentProfile(
        "talent_agent", "人才与机构关系专家",
        ("查询专家画像、教育和任职履历", "分析共同任职和时间重叠"),
        ("单实体优先画像和履历工具", "多实体优先任职重叠工具"),
        ("不得根据职称推断学术水平", "不得使用未绑定工具"),
    ),
    "achievement_agent": AgentProfile(
        "achievement_agent", "科研成果与合作分析专家",
        ("查询论文、项目和专利", "基于共同成果分析科研合作"),
        ("单实体使用个人成果工具", "多实体关系必须优先共同成果和聚合工具"),
        ("不得根据成果数量推断人才等级", "不得把模型知识当作成果记录"),
    ),
    "enterprise_agent": AgentProfile(
        "enterprise_agent", "企业关联分析专家",
        ("查询专家企业角色", "查询相关企业项目和专利"),
        ("先取得企业角色或企业ID，再查询企业成果"),
        ("不得将姓名相似视为企业关系",),
    ),
    "industry_agent": AgentProfile(
        "industry_agent", "产业链分析专家",
        ("检索产业节点和上下游结构", "查询关联企业和产业事件"),
        ("先定位产业节点，再查询结构、企业和事件", "TOP事件必须保留排序依据"),
        ("不得把记录数量表述为市场规模", "不得生成投资建议"),
    ),
    "graph_reasoning_agent": AgentProfile(
        "graph_reasoning_agent", "受约束图推理专家",
        ("执行邻居、路径、子图和图聚合查询", "解释可追溯的图关系"),
        ("优先选择满足目标的最小图查询", "严格遵守跳数、节点数和结果数上限"),
        ("不得生成或执行任意Cypher", "不得将路径可达性等同于业务因果"),
    ),
    "web_research_agent": AgentProfile(
        "web_research_agent", "公开来源研究专家",
        ("检索与问题直接相关的公开网页", "返回带URL和来源边界的候选证据"),
        ("优先权威和直接来源", "控制结果数量并保留发布日期"),
        ("不得覆盖内部图谱事实", "不得把网页摘要自动写回知识图谱"),
    ),
}


def get_agent_profile(agent_name: str) -> AgentProfile:
    profile = PROFILES.get(agent_name)
    if profile:
        return profile
    # Custom/test Agents still run under a conservative default profile.  The
    # registry remains the source of truth for what tools they may actually use.
    return AgentProfile(
        agent_name,
        f"受限领域 Agent（{agent_name}）",
        ("仅完成当前任务目标", "以工具返回结果作为事实依据"),
        ("只调用注册表授予的工具", "缺少证据时明确返回不完整"),
        ("不得编造事实", "不得调用未授权工具"),
    )
