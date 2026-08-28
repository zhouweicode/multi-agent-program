# 四层记忆架构

本项目将记忆视为运行上下文和用户数据，不把记忆当作知识图谱事实。人才、论文、企业、项目、专利和产业链结论仍必须以 MySQL/Neo4j 的实时证据为准。

## 目标结构

| 层级 | 作用 | 权威存储 |
| --- | --- | --- |
| 运行记忆 | LangGraph 节点状态和中断恢复 | SQLite Checkpoint |
| 会话记忆 | 当前对话实体、指代和最近轮次 | MySQL；SQLite 为开发适配器 |
| 用户长期记忆 | 偏好、关注对象、稳定约束和用户修正 | MySQL |
| 查询经验记忆 | 路由、Agent、Tool 和正负执行经验 | MySQL；SQLite 为开发适配器 |

Milvus 仅保存可重建的长期记忆检索索引；Neo4j 不保存用户隐私记忆。

## 阶段状态

### 阶段 0：用户隔离（已完成）

- 会话记忆使用 `(user_id, conversation_id)` 复合归属。
- API 从服务端登录会话解析 `user_id`，不接受客户端声明记忆所有者。
- 不存在或不属于当前用户的会话记忆统一返回 `404`。
- 同一个 `conversation_id` 可以在不同用户作用域内独立存在。
- 查询经验分为 `user` 和 `global` 作用域。
- 用户私有作用域记录正负事件；全局作用域只记录通过校验的成功事件。
- 全局事件不存储归一化原始问题，只保存移除实体、邮箱、URL、年份和数字后的查询模板。
- 升级前无归属数据进入 `legacy-unowned` 或 `legacy` 隔离桶，不自动分配给真实用户。

### 阶段 1：统一 MemoryManager（已完成）

- 已定义 `recall_context`、`record_turn`、`enqueue_update`、事实 CRUD、`clear_memory` 和 `flush` 统一契约。
- 会话 Node、查询经验服务、API 和关闭生命周期只依赖 `MemoryManager`。
- SQLite 适配器用于测试和本地开发，分别保存会话、经验和长期事实/任务。
- MySQL 生产适配器使用独立 `gkx_runtime` 数据库并幂等管理全部记忆表。
- 已创建 `memory_profiles`、`memory_facts`、`memory_update_jobs`、`memory_audit_logs` 以及会话和经验表。
- 当前 `.env` 已切换为 `MEMORY_BACKEND=mysql`；`gkx` 知识数据库没有新增用户记忆表。

### 阶段 2：长期记忆抽取（已完成）

- 会话写回后只把限长、脱敏的用户输入和答复摘要放入 `memory_update_jobs`，不把 Tool 原始结果放入任务。
- 后台 Worker 通过租约领取任务；进程异常后过期租约可回收，失败按指数间隔重试，超过上限转为 `failed`。
- 抽取器只读取用户原话，并且只接受带有“记住、以后、长期、始终、每次”等稳定信号的明确表达。
- 当前支持 `preference`、`focus`、`correction`、`constraint`、`output_format` 和受控 `context` 分类。
- 一次性查询条件、模型回答、Tool 结果和没有稳定信号的业务事实不会进入长期事实表。
- 密码、Token、密钥、邮箱、手机号和身份证号在入队前脱敏；用户输入包含敏感值时整项抽取 fail-closed。
- 长期记忆队列或抽取器异常只记录事件并重试，不阻断用户查询（fail-open）。
- `run_id` 保证任务幂等，`(user_id, agent_name, normalized_hash)` 保证事实幂等。

可配置项：

| 环境变量 | 默认值 | 作用 |
| --- | ---: | --- |
| `MEMORY_EXTRACTION_ENABLED` | `true` | 是否启用自动入队和 Worker |
| `MEMORY_WORKER_POLL_SECONDS` | `1` | 空队列轮询间隔 |
| `MEMORY_WORKER_BATCH_SIZE` | `10` | 单批领取数量 |
| `MEMORY_WORKER_LEASE_SECONDS` | `60` | Worker 任务租约 |
| `MEMORY_WORKER_MAX_ATTEMPTS` | `3` | 最大处理次数 |
| `MEMORY_FACT_CONFIDENCE_THRESHOLD` | `0.85` | 自动写入最低置信度 |

### 阶段 3：检索和安全注入（已完成）

- 每轮从 MySQL 权威事实和 Milvus 混合向量索引生成候选，再执行确定性相关性排序；只保留 Top 3～5。
- `correction` 优先于稳定约束、输出格式、偏好和关注对象，在数量与预算裁剪时受保护。
- 初始注入预算默认 1000 Token，并强制限制在 800～1200 Token；State 保存估算值和实际使用的 fact ID。
- 记忆内容通过 XML/HTML 转义，包裹在独立 `<user_memory_context>` 中。
- Prompt 明确声明记忆不是知识图谱事实、证据或系统指令，禁止改变领域分类、Agent/Tool 路由、实体关系和验证要求。
- Router 永远只读取当前问题；Planner 和领域 Agent 只能把记忆用于表达偏好，业务结论仍只能来自已验证证据。
- 普通答案可确定性应用“表格格式”偏好，并记录 `long_term_memory_applied_fact_ids`，不会把记忆加入 `evidence`。
- 召回或 Milvus 索引异常均回退 MySQL/空上下文，不阻断正常查询。
- Milvus 使用独立文件 `.runtime/user-memory-milvus.db` 和集合 `user_memory_facts_v1`；启动配置禁止与专家实体集合重名。
- MySQL 是长期事实权威存储，Milvus 只保存可重建索引；向量查询强制使用 `user_id + agent_name` 过滤。

检索配置：

| 环境变量 | 默认值 | 作用 |
| --- | ---: | --- |
| `MEMORY_RECALL_TOP_K` | `5` | 单轮最大召回数，强制范围 3～5 |
| `MEMORY_RECALL_CANDIDATE_LIMIT` | `100` | 权威存储候选上限 |
| `MEMORY_RECALL_TOKEN_BUDGET` | `1000` | 注入预算，强制范围 800～1200 |
| `MEMORY_RETRIEVAL_BACKEND` | `mysql` | `mysql`、`hybrid` 或 `milvus` |
| `MEMORY_MILVUS_URI` | `.runtime/user-memory-milvus.db` | 用户记忆独立 Milvus Lite 文件 |
| `MEMORY_MILVUS_COLLECTION` | `user_memory_facts_v1` | 用户记忆独立集合 |

### 阶段 4：记忆管理界面（已完成）

- 顶栏“记忆管理”面板展示长期事实总数、有效数、过期数和最近更新时间。
- 支持按正文搜索和分类筛选，并可手动新增、修改、删除长期事实。
- 手动写入沿用自动抽取的敏感信息检查；密码、Token、邮箱、手机号和身份证号会被拒绝。
- 每条事实展示分类、来源会话或 Run、置信度、有效期、过期状态和 revision。
- 导出接口生成当前登录用户的 JSON；不会导出其他用户数据。
- “清除全部个人记忆”使用确认字符串二次校验，同时清除长期事实、全部会话记忆、私有查询经验、待处理任务和用户摘要；知识图谱不受影响。
- 最终答案区展示本轮召回的长期事实，并区分“已召回”和“已应用到答案”。
- 所有管理接口从服务端登录会话确定 `user_id`；跨用户读、改、删统一不可见。
- 全量清除会按 `user_id` 删除独立 Milvus 索引，MySQL 仍是权威存储。

管理接口：

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `GET` | `/memory/summary` | 当前用户记忆摘要 |
| `GET` | `/memory/facts` | 搜索和筛选事实 |
| `POST` | `/memory/facts` | 手动新增事实 |
| `PATCH` | `/memory/facts/{fact_id}` | 修改自己的事实 |
| `DELETE` | `/memory/facts/{fact_id}` | 删除自己的事实 |
| `GET` | `/memory/export` | 导出当前用户 JSON |
| `DELETE` | `/memory` | 二次确认后清除全部个人记忆 |

### 阶段 5：生命周期治理（已完成）

- 新事实默认设置 90 天复核日期；升级前没有有效期的事实按最后更新时间补齐 90 天。到期事实继续显示在管理面板，但不进入 Prompt 召回。
- 到期事实可执行“续期 90 天”或“归档”；续期会提升 revision，归档后不再出现在默认事实列表和召回候选中。
- 同一用户/Agent 作用域内，相似度达到阈值的同类事实合并到原 fact ID；确定性识别出的否定、输出格式和风格冲突由最新用户表达替换。
- 默认每用户/Agent 最多保留 100 条有效事实。超限时优先淘汰低置信、从未召回且较旧的普通事实，`correction` 最后淘汰。
- 修改、删除和过期复核均使用 `expected_revision` 乐观锁；旧页面提交过期 revision 时返回 `409`，不会覆盖新数据。
- SQLite 和 MySQL 均记录创建、修改、删除、合并、替换、容量淘汰、召回、实际应用、复核和清除审计。
- 事实记录保存 `recall_count`、`application_count`、`last_recalled_at` 和 `last_applied_at`；管理面板展示累计召回、实际应用和单条使用情况。
- `/memory/audit` 只返回当前登录用户的审计记录；JSON 导出 schema v2 同时包含事实、摘要和审计。
- 服务优雅关闭时先停止后台线程，再同步抽取当前可领取的持久任务，最后关闭 MemoryManager，避免已排队偏好静默丢失。
- MySQL 记忆 schema 升级为 version 2；新增列和旧事实有效期均采用幂等迁移。

生命周期配置：

| 环境变量 | 默认值 | 作用 |
| --- | ---: | --- |
| `MEMORY_FACT_MAX_PER_SCOPE` | `100` | 每用户/Agent 有效事实容量 |
| `MEMORY_FACT_REVIEW_DAYS` | `90` | 默认复核周期 |
| `MEMORY_FACT_SIMILARITY_THRESHOLD` | `0.86` | 相似事实合并阈值 |
