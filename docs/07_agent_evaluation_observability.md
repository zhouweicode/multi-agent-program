# Agent 评测与统一可观测平台

这一模块回答一个核心问题：如何证明 Agent 系统有效，而不只是“看起来能运行”。实现由在线 Trace、
离线黄金集、CI 门禁和双 Run 对比四部分组成。

## 1. 统一 Trace

每次创建或恢复查询时生成新的 `trace_id` 和 `attempt_id`，同一个业务 `run_id` 可以包含多个 Attempt。
Trace Context 通过 Python `contextvars` 在后台线程和 LangGraph Node 间传递，并通过 MCP request metadata
继续传播到 MCP Server。

```text
API request
└── graphrag.workflow.execute
    ├── langgraph.node.router
    ├── langgraph.node.entity_resolution
    ├── langgraph.node.supervisor
    ├── langgraph.node.<domain_agent>
    │   └── agent.<agent_name>
    │       ├── agent.model.invoke
    │       │   └── gen_ai.chat
    │       └── tool.<tool_name>
    │           ├── mcp.client.<tool_name>
    │           └── mcp.server.<tool_name>
    │               └── db.mysql / db.neo4j / db.milvus
    ├── langgraph.node.merge
    ├── langgraph.node.validator
    └── langgraph.node.answer
```

SQLite 使用 WAL 模式持久化 `trace_runs` 和 `trace_spans`。Span 保存父子关系、类型、状态、起止时间、
耗时、Token、成本、错误类型和经过裁剪的业务属性。现有 SSE 事件仍用于实时 UI，且自动附带
`trace_id`、`span_id` 和 `attempt_id`，二者可以交叉定位。

## 2. Token、成本和运行指标

真实 ChatModel 通过 LangChain Callback 读取 Provider 返回的 usage：

```text
cost = input_tokens × input_price / 1,000,000
     + output_tokens × output_price / 1,000,000
```

配置：

```env
OBSERVABILITY_DB_PATH=.runtime/observability.sqlite
WORKFLOW_VERSION=stage10.1
PROMPT_VERSION=prompt-v1
MODEL_INPUT_COST_PER_MILLION=0
MODEL_OUTPUT_COST_PER_MILLION=0
MODEL_COST_CURRENCY=USD
```

`GET /observability/summary` 汇总 Run 数、成功率、错误率、超时率、P95/平均延迟、Token、总成本、
平均成本、工具成功率和平均重规划次数。`GET /observability/runs/{run_id}` 返回完整 Attempt 与 Span。
Mock Provider 不产生真实 Token usage，因此离线测试成本为 0，不能用它评估生产模型成本。

## 3. 50 条黄金集

`evals/golden_v1.jsonl` 是逐行 JSON，共 50 条：

- 10 条实体消歧：重名 Recall@10、唯一实体自动确认 Precision、负样本；
- 20 条路由：主领域、复杂度、实体 mention、Verification 和联网开关策略；
- 20 条端到端：Agent 集合、Tool 集合、证据完整性、引用有效性、答案覆盖和规则校验。

离线运行：

```bash
python -m scripts.run_agent_evals
python -m scripts.run_agent_evals --check
```

输出报告默认写入 `.runtime/eval-report.json`。评测使用 Mock 后端保持确定性；`--live` 可用于真实模型和
数据库的实验评测，但真实评测不应直接取代 CI 的可重复基线。

## 4. CI 回归门禁

基线位于 `evals/baselines/agentops_v1.json`，包含三类约束：

1. `minimums`：质量指标的绝对下限；
2. `maximums`：P95、重规划和成本的绝对上限；
3. `regression_metrics`：相对上一基线默认最多下降 0.02。

GitHub Actions 在全部单测后执行黄金集。门禁失败会返回非零退出码阻止合并；无论成功或失败，
`.runtime/eval-report.json` 都作为 Artifact 上传，方便定位具体失败 Case。

## 5. 双 Run 对比

前端页面底部的“双 Run 对比”读取最近 30 个持久化 Run。选择基准与候选 Run 后，页面并排展示：

- 模型、Prompt、工作流版本；
- 总耗时、Token、模型成本、工具成功率、重规划和错误数；
- 两侧按耗时降序排列的 Span；
- API 返回的仅左侧/仅右侧 Span 差异。

后端接口：

```text
GET /observability/compare?left_run_id=<baseline>&right_run_id=<candidate>
```

生产接入新模型或修改 Prompt 时，应同时更新 `MODEL_NAME`、`PROMPT_VERSION` 或 `WORKFLOW_VERSION`，
否则 Run 对比虽能看到指标变化，却无法可靠追踪变化来源。

## 6. 边界

当前 Trace 协议是项目内的 vendor-neutral 实现，数据落在本地 SQLite，尚未发送到第三方平台。
下一步可增加 OTLP Exporter，将同一套 Trace 输出到 OpenTelemetry Collector，再接 Grafana Tempo、
Jaeger 或商业观测平台；业务 Node 和 Agent 无需改变。
