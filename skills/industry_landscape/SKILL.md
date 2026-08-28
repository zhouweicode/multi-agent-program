---
id: industry_landscape
version: 1.1.0
name: 产业全景报告
description: 基于产业链结构、关联企业、重点事件和可选公开来源生成可追溯产业全景报告。
enabled: true
trigger_phrases:
  - 产业全景报告
  - 产业链全景报告
  - 行业全景报告
  - 产业研究报告
required_capabilities:
  - industry_landscape_core
optional_capabilities:
  - external_industry_evidence
input_schema: skills.industry_landscape.schemas:IndustryLandscapeInput
output_schema: skills.industry_landscape.schemas:IndustryLandscapeReport
default_input:
  report_type: comprehensive
  audience: internal
  industry_query: ""
  include_web: false
  top_n_companies: 10
  top_n_events: 10
evaluation:
  dataset: evals/industry_landscape_cases.json
  baseline: evals/baselines/industry_landscape_v1.json
  runner: evaluation.industry_landscape_runner:evaluate_industry_landscape_dataset
  gate: evaluation.industry_landscape_runner:evaluate_industry_landscape_gate
---

# 产业全景报告 Skill

## 目标

基于产业节点检索、产业链结构、关联企业、重点事件和可选公开网页证据，生成可追溯的产业全景报告。Skill 不直接调用 Agent 或 Tool；Supervisor 将能力声明展开为领域任务，统一 Agent Harness 负责执行。

## 输入

- `industry_query`：产业或行业名称；允许为空，为空时由 IndustryAgent 从问题和高事件量节点中确定范围。
- `report_type`：`brief` 或 `comprehensive`。
- `audience`：`internal`、`government`、`investment`、`enterprise` 或自定义值。
- `include_web`：是否补充公开网页候选证据。
- `top_n_companies`、`top_n_events`：展示上限，范围 1–50。

## 执行方法

1. IndustryAgent 必须先检索产业节点，再获取产业链结构、关联企业和重点事件。
2. 明确要求联网且系统允许联网时，由 WebResearchAgent 补充公开来源。
3. 所有 Tool 调用通过现有 Agent Harness 执行，Skill 不持有 Tool 权限。
4. Composer 只读取 Validator 已通过的领域结果，不调用模型或 Tool。
5. 每条分析性陈述必须绑定本次 State 中存在的 `evidence_id`。
6. 产业内部数据属于核心域，失败时有限重规划；联网属于可选域，失败时降级并标注缺口。

## 输出

- 范围与产业节点概览；
- 上中下游或关联节点结构；
- 相关企业；
- 重点产业事件；
- 可选公开来源；
- 关键数据信号、风险与数据缺口；
- 证据覆盖率与证据目录。

## 质量检查

- `IndustryClaim.evidence_ids` 不得为空，且必须出现在 `evidence_catalog` 中。
- 不把事件数量、重要度等记录直接解释为市场规模、竞争力或投资价值。
- 不生成无证据市场预测、企业排名或政策结论。
- 公开网页只作为候选证据，不覆盖图谱事实，也不自动回写知识图谱。
