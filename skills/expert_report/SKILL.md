# 专家报告 Skill

## 目标

基于知识图谱、结构化业务库和可选公开网页证据，为已完成实体消歧的单个专家生成可追溯报告。Skill 不直接调用 Agent 或 Tool；它声明能力需求，由 Supervisor 展开任务并交给统一 Agent Harness 执行。

## 输入

- 必需：单个规范专家实体 ID。
- `report_type`：`brief` 或 `comprehensive`。
- `audience`：`internal`、`government`、`enterprise` 或调用方自定义值。
- `include_enterprise`、`include_cooperation_network`、`include_web`。
- `top_n`：成果与外部来源的最大展示条数，范围 1–50。

## 执行方法

1. 核心能力必须包含专家画像与履历、科研成果。
2. 完整报告可并行补充企业关系和合作网络；明确要求联网时补充公开网页候选证据。
3. 所有领域查询通过已有 Supervisor、Scheduler 和 Agent Harness 执行。
4. Composer 只读取 Validator 已通过的领域结果；不得调用业务 Tool，不得引入领域结果之外的事实。
5. 分析性陈述必须绑定至少一个 `evidence_id`。无证据字段只能作为“数据缺口”，不能写成肯定结论。
6. 企业、合作网络和联网属于可降级扩展域；失败时保留核心报告并明确标注缺口。画像或科研成果失败时不生成可靠报告。

## 输出

输出 `ExpertReport` 结构化对象和等价 Markdown：

- 报告元数据与执行摘要；
- 画像履历、科研成果，以及可选企业关系、合作网络、公开来源章节；
- 基于证据的优势摘要；
- 风险与数据缺口；
- 证据覆盖率和证据目录。

## 质量检查

- 每条 `ReportClaim` 至少包含一个在本次 State 证据集中存在的 ID。
- Markdown 引用必须能回指结构化报告的证据目录。
- 不将网页候选证据自动写回知识图谱，也不覆盖内部图谱事实。
- 不输出模型臆测、无证据排名、人格判断或敏感属性推断。
