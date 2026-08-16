# 第七阶段：任务契约与后台运行架构

第七阶段不增加新的 Agent，而是提高现有 Multi-Agent GraphRAG 的正确性、可验证性和运行稳定性。

## Task Contract

`PlannedTask` 除了 `task_id`、`agent`、`goal`，还包含：

```json
{
  "required_fact_types": ["common_papers", "common_projects", "cooperation_summary"],
  "required_entity_ids": ["person_zw_001", "person_lm_001"]
}
```

Supervisor 仍然只负责规划，不调用 Tool。`models/contracts.py` 把业务事实类型映射到领域 Tool；Rule Validator 根据任务契约检查 DomainResult 是否返回必要事实，再执行作者、参与者、时间和 count 等数据级校验。

## 统一 Verification 数据链

Verification Tools 不再直接导入论文和项目 Mock 常量。调用链为：

```text
VerificationAgent
→ verification_tools
→ EvidenceService
→ AchievementService
→ Mock 或 MySQL Repository
```

因此 AchievementAgent 和 VerificationAgent 会读取同一后端、同一批实体 ID 和证据 ID。

## 后台 Run 与 run_id

`POST /queries` 不再等待整个 LangGraph 完成：

```text
POST /queries
→ 创建唯一 run_id
→ RunManager 提交后台任务
→ 立即返回 202 RUNNING
```

相同 `run_id` 不能创建第二个问题，避免 SQLite Checkpoint 中的旧 State 合入新问题。实体消歧时状态变为 `NEED_USER_SELECTION`；调用 resume 后，同一个 Run 从 LangGraph interrupt 恢复。

## SSE 事件

前端连接 `GET /queries/{run_id}/stream`。服务端发送：

- `trace`：Router、Node、Agent、Tool、Validator 等事件；
- `status`：`NEED_USER_SELECTION`、`COMPLETED` 或 `FAILED`。

兼容接口 `/events?after=cursor` 仍然保留。

## 可观测性边界

节点快照会递归隐藏 password、secret、api_key、token 和 authorization 字段；单个快照、单线程事件数和内存中的线程数均有限制。生产环境默认只记录事件摘要，不把完整 State 写入日志。

## Service 生命周期

`services/resources.py` 在进程内复用 EntityService、AchievementService 和 GraphService。FastAPI shutdown 时关闭 Milvus Client、Neo4j Driver 和 SQLite Connection，避免每次 Tool Call 新建图数据库 Driver。

## Answer Formatter

Answer Node 仍然不是 Agent。五个领域的确定性解释被拆到 `formatters/`，Answer Node 只负责组合通过校验的领域段落、综合结论和 Verification 结论。
