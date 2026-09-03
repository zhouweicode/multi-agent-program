# Agent 运行时九项优化

本轮改造的目标不是增加更多 Agent，而是让已有领域 Agent 在复杂任务、故障和真实模型波动下仍然可控、
可验收、可观测。九项能力均进入实际运行链路，并有专项测试覆盖。

## 1. 从 Agent 调度改为任务实例调度

复杂计划以 `(replan_generation, task_id)` 作为执行和完成键。Scheduler 返回 LangGraph `Send`，每个
`PlannedTask` 携带自己的目标、依赖、必需事实类型和 Tool 偏好进入通用 `task_executor`。因此同一个
Agent 可以在同一计划中执行多个独立任务，不再因“按 Agent 取第一个任务”而丢失工作。

执行结果先写入 `task_results`，Validator 和任务历史按具体 `task_id` 验收；完成后再聚合到旧的
`talent_result`、`achievement_result` 等字段，兼容回答层和已有 Checkpoint。

## 2. 声明式 Agent Profile

`agents/profiles.py` 集中描述六个领域 Agent 的角色、职责、Tool 策略和禁止事项。Profile 进入 System
Message，Tool Registry 仍是实际权限边界。未登记的扩展或测试 Agent 使用最小权限默认 Profile，
不会取得额外工具。

## 3. 任务级检索计划

`RetrievalPlan` 把任务目标、`required_fact_types`、候选 Tool、历史建议 Tool 和停止条件显式传给模型。
候选 Tool 由 FactType 契约映射，并与当前 Agent 实际绑定 Tool 求交集；历史经验不能注入未授权 Tool。

## 4. 完成门禁与无进展检测

Agent 返回无 Tool Call 的消息时，`RequiredFactsCompletionPolicy` 检查每类必需事实是否已有成功
Observation。缺失时向模型反馈结构化 `INCOMPLETE`，要求继续检索；连续提前结束则以
`AGENT_INCOMPLETE` 停止。

`ProgressDetectionMiddleware` 对成功 Observation 做内容指纹。连续空结果、失败结果或重复结果达到
阈值后以 `AGENT_NO_PROGRESS` 停止，避免仅通过改变参数绕过重复调用检测。

## 5. 按结论类型选择 Verification Policy

Verification 不再只验证“核心科研合作伙伴”。当前支持科研合作、企业关系、产业关联、图路径和公开来源
事实等 Policy。每个 Policy 声明允许采信的来源 Tool、验证 Tool 序列、关系类型和约束；验证节点只把
与该 Policy 匹配的 EvidenceRecord 交给 Verification Agent。

规则 Validator 继续处理实体、字段、时间、计数等确定性条件；Verification Agent 只处理明确触发的
关系结论，不替代规则校验。

## 6. 同轮 Tool 并发和异步入口

真实模型一次返回多个相互独立 Tool Call 时，Harness 使用有界线程池并发执行，并按模型原始顺序写回
ToolMessage。`max_parallel_tools` 限制并发度，调用预算、Middleware、回执和 Observation 压缩仍逐项生效。

`AgentHarness.aexecute()` 为异步服务提供非阻塞入口；原同步 API 保持兼容。原生异步 LangChain Tool
通过 `ainvoke` 执行并接受单次超时约束。

## 7. 模型和 Tool 韧性治理

模型调用增加独立超时、错误分类和有限指数退避重试。Tool 仅在超时、限流或提供方不可用等瞬态错误上
重试；元数据为 `idempotent=false` 的 Tool 永不自动重试。连续瞬态失败会打开进程内熔断器，并在恢复
窗口后半开重试。重试等待和调用边界都会检查 Run 取消信号。

需要注意：当前熔断状态是单进程内存状态，不是 Redis 分布式熔断；同步 Provider 超时后由守护线程隔离，
Python 无法强制终止已进入第三方阻塞调用的线程。生产 Provider 应同时配置客户端原生超时。

## 8. 查询经验分级辅助

`QUERY_EXPERIENCE_MODE` 支持三档：

- `shadow`：只召回、评分和对比，不影响本次计划；
- `advisory`：为当前安全计划附加经过 Tool Registry 白名单过滤的 Tool 偏好；
- `active`：除 Tool 偏好外，仅在历史路由与当前路由一致时调整同一 Agent 集合的任务顺序。

经验不能增加或删除领域、不能跳过 Router/Validator、不能在 Replan 阶段接管计划，负样本也不会被推荐。

## 9. 真实模型小样本重复评测

`scripts/run_live_agent_evals.py` 从黄金集选择有限的 routing/workflow Case，多次运行以检测随机波动，报告：

- Case 通过率、路由稳定性和工作流稳定性；
- P95/平均延迟、平均 Replan；
- 非法 Tool 调用率、Agent 不完整结束率、无进展停止率。

入口默认拒绝 Mock Provider，避免把确定性 Mock 分数误报为真实模型效果。真实评测是实验报告；默认 CI
仍使用 Mock 黄金集保证可重复性。

## 配置

Harness 的全局变量包括：

```env
AGENT_MODEL_TIMEOUT_SECONDS=65
AGENT_MODEL_MAX_RETRIES=1
AGENT_TOOL_TIMEOUT_SECONDS=30
AGENT_TOOL_MAX_RETRIES=1
AGENT_RETRY_BASE_SECONDS=0.05
AGENT_RETRY_MAX_SECONDS=2
AGENT_RETRY_JITTER_SECONDS=0.05
AGENT_CIRCUIT_BREAKER_THRESHOLD=5
AGENT_CIRCUIT_BREAKER_RESET_SECONDS=30
AGENT_PARALLEL_TOOL_CALLS=true
AGENT_MAX_PARALLEL_TOOLS=4
AGENT_NO_PROGRESS_THRESHOLD=3
```

每个变量都可用 `<AGENT_NAME>_` 前缀覆盖，例如
`WEB_RESEARCH_AGENT_MODEL_TIMEOUT_SECONDS=30`。
