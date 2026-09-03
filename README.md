# 亿级科技知识图谱 Multi-Agent GraphRAG

这是一个可运行、可调试、用于学习与面试讲解的 Multi-Agent GraphRAG 示例。当前版本在统一 EvidenceRecord 和共享 Agent Harness 之上增加运行时 Skill：`expert_report` 生成证据化专家报告，`industry_landscape` 生成产业链、关联企业和重点事件全景报告。

第六阶段新增统一 canonical entity ID、Dense/Sparse Embedding Provider、Milvus Lite Hybrid Search 和 RRF 实体召回。Shared State 只保存 canonical ID，各数据库内部 ID 只在 Service/Repository 边界转换。

## 架构边界

- Router、Entity Resolution、Supervisor、Merge、Rule Validator、Answer 都是 LangGraph Node，不是 Agent。
- 已实现 TalentOrganizationAgent、ResearchAchievementAgent、EnterpriseRelationAgent、IndustryChainAgent、GraphReasoningAgent 和 WebResearchAgent 六个领域 Agent。
- Domain Agent 与 VerificationAgent 复用同一个 `AgentHarness`：统一执行 Tool Calling、Middleware、循环检测、错误分类、超时重试和 Observation 压缩，并通过工具白名单阻止跨领域调用。
- `ModelFactory` 支持全局及每 Agent 的 OpenAI-Compatible 模型配置，业务代码不绑定具体模型 SDK。
- Entity Resolution 使用 LangGraph `interrupt()` 暂停，使用同一 `thread_id` 和 `Command(resume=...)` 恢复。
- Supervisor 根据复杂 Query 生成结构化 `tasks`；调度器按 `execution_mode` 和 `depends_on` 执行 sequential 或依赖就绪的 parallel 波次，全部完成后再 Merge。
- 每个复杂任务携带 `required_fact_types` 与 `required_entity_ids`；Rule Validator 按任务契约验收。
- Validation 分为两层：Rule Validator 负责确定性校验；VerificationAgent 仅在 `requires_verification=true` 的复杂语义判断中执行。
- Harness 的局部 Messages 不写入全局 State；完整 Tool Observation 留给事实与证据，压缩副本只回送模型，避免上下文膨胀。
- Supervisor 重规划时读取 `missing_domains`、`missing_evidence` 和 `task_history`，只补调缺失领域，并受 `max_replans` 限制。
- FastAPI 使用后台 Run 执行 LangGraph，`POST /queries` 立即返回 `RUNNING`，前端通过 SSE 接收轨迹和终态。
- FastAPI 提供三用户登录、HttpOnly Cookie 会话与 API 认证；用户、密码哈希和会话保存在独立 SQLite，当前 `user_id` 会进入每次 GraphRAG State。
- `run_id` 与一次执行绑定并禁止复用，避免旧 Checkpoint State 污染新问题。
- 节点轨迹包含脱敏、限长的输入输出快照，并限制线程数和单线程事件数。
- Entity Resolution 支持 `hybrid`：MySQL 权威精确召回、BGE-M3 + Milvus 语义补召回、上下文重排和置信阈值。
- 后台 Run 状态、interrupt、错误和耗时持久化到独立 SQLite Registry，并支持取消和超时。
- Tool Observation 在写入 Shared State 前转换为统一证据记录；证据明细保留在 State 与执行轨迹中供审计，最终答案不展开内部证据编号。
- 企业和产业领域可独立切换到 Neo4j，并复用 GraphService 的同一 Driver。
- 领域Tool支持`local/mcp`双传输：local模式进程内调用；MCP模式从独立Server动态发现白名单工具并通过Streamable HTTP执行。
- MCP Server复用现有Service/Repository，公开人才、成果、企业、产业、图检索、证据验证和可选联网搜索共七组能力；LangGraph控制节点保持本地。
- WebResearchAgent仅在问题明确要求联网、最新资料、官网、新闻或外部查证时执行；网页结果作为带URL的外部候选证据，不覆盖图谱事实，也不自动回写图谱。
- 对话记忆以 `(user_id, conversation_id)` 隔离并跨多个 Run 保存已确认实体；Memory Node 在 Router 前解析“他/她/该教授”等指代，在 Answer 后写回，并与知识图谱事实库隔离。
- 查询经验记忆区分用户私有作用域与脱敏全局作用域：每个用户保留自己的正负经验，只有通过校验且完成模板脱敏的成功策略才进入全局共享；默认 `Shadow`，不绕过 Router、Supervisor 或 Validator。
- 统一 `MemoryManager` 隔离业务层与存储实现：生产配置将会话、长期事实和查询经验写入独立 MySQL `gkx_runtime`，SQLite 适配器仅用于本地开发和测试。
- 长期记忆采用被动异步抽取：用户输入先脱敏进入持久任务队列，后台 Worker 只保存明确的偏好、长期关注、修正、稳定约束和固定输出格式；失败不影响主查询。
- 长期记忆召回采用 MySQL 权威事实与独立 Milvus `user_memory_facts_v1` 混合索引，按相关性与类别优先级选取 Top 3～5；注入前进行 XML 转义和 800～1200 Token 预算控制，且不能充当知识图谱证据或改变 Agent/Tool 路由。
- 顶栏“记忆管理”按当前登录用户提供长期事实摘要、搜索、手动增删改、JSON 导出和二次确认的全量清除；答案区可追踪本轮召回及实际应用的记忆。
- 长期事实采用 90 天复核、相似合并、冲突替换和每用户/Agent 100 条容量治理；修改、删除及复核使用 revision 乐观锁，并记录召回、应用与完整写入审计。
- `expert_report` Skill 只声明 SOP、能力需求和输出协议；Supervisor 将能力展开为现有 Agent 任务，确定性 Composer 不持有 Tool 权限，只消费已验证证据，并支持企业、网络和联网章节降级。
- `industry_landscape` Skill 复用 IndustryAgent 获取产业节点、链结构、关联企业和事件，并可选调用 WebResearchAgent；报告不会把记录数量和事件重要度臆测为市场规模或投资结论。

## 领域能力

| Agent | Tools 与可切换后端 |
|---|---|
| TalentOrganizationAgent | `get_person_profile`、`get_employment_history`、`get_education_history`、`match_employment_overlap` |
| ResearchAchievementAgent | `get_author_papers`、`get_common_papers`、`get_common_projects`、`get_person_patents`、`get_common_patents`、`aggregate_cooperation` |
| EnterpriseRelationAgent | `get_person_company_roles`、`get_company_projects`、`get_company_patents` |
| IndustryChainAgent | `get_chain_structure`、`get_node_companies`、`get_node_events`、`rank_top_events` |
| GraphReasoningAgent | 基础：`get_neighbors`、`find_path`、`k_hop_expand`、`calculate_path_strength`；高级：`get_neighbors_filtered`、`find_paths`、`query_subgraph`、`aggregate_graph`、`get_graph_schema` |
| WebResearchAgent | `search_web`（Brave/Tavily，可通过 local 或 MCP 调用） |
| VerificationAgent | `verify_evidence`、`check_source`、`get_cooperation_timeline`、`validate_relation`、`check_constraints` |

## 第五阶段数据仓储

- `MySQLRepository`：只读接入 `gkx.dwd_scholar`、论文、项目和专利表，支持学者、任职、教育、单人/共同论文、共同项目及单人/共同专利查询。
- `Neo4jGraphRepository`：只读实现一跳邻居、Top-K 路径、K 跳扩展、受限子图、图聚合和路径强度；所有动态 Label、关系和属性先经过治理 Schema 白名单，值仅通过参数传入。
- `EntityService`、`AchievementService`、`GraphService` 负责选择 Repository；Agent 与 Tool Schema 无须感知底层数据库。
- 三类后端可独立切换；当前本地配置默认实体检索为 Milvus，科研成果和图查询仍可保持 Mock。自动化测试强制使用轻量 Mock，不要求数据库在线。

本机开发环境已支持 Milvus Lite。它使用与 Milvus Standalone 相同的 `MilvusClient` API，适合先实现 BGE-M3 Dense/Sparse 与 Hybrid Search：

```python
from pymilvus import MilvusClient

client = MilvusClient(".runtime/milvus.db")
```

Milvus Lite 数据文件应放在已忽略的 `.runtime/` 中，不提交到 Git。后续迁移到 Standalone 时，将 URI 改为 `http://127.0.0.1:19530` 即可。

项目使用 `GRAPHRAG_MILVUS_URI`，不要用 `MILVUS_URI` 表示 Lite 文件路径，因为后者会被 `pymilvus` SDK 自己读取并按 HTTP 地址解析。

## 第八阶段混合实体检索

```mermaid
flowchart LR
    Q[Entity Mention + Query Context] --> SQL[MySQL 精确召回]
    Q --> E[Embedding Provider]
    E --> D[Dense Vector]
    E --> S[Sparse Vector]
    D --> MH[Milvus Hybrid Search]
    S --> MH
    MH --> RRF[RRF Fusion k=60]
    RRF --> C[候选融合与可解释重排]
    SQL --> C
    C --> ER[Entity Resolution Node]
    ER -->|唯一| CID[Canonical entity_id]
    ER -->|重名| UI[NEED_USER_SELECTION]
    CID --> MAP[Entity ID Mapping]
    MAP --> MY[MySQL scholar_id]
    MAP --> NEO[Neo4j scholar_id]
```

## 第九阶段真实数据与统一证据

领域后端可以独立切换：

```env
ENTITY_BACKEND=hybrid
ACHIEVEMENT_BACKEND=mysql
GRAPH_BACKEND=neo4j
ENTERPRISE_BACKEND=neo4j
INDUSTRY_BACKEND=neo4j
```

统一证据结构包含 `evidence_id`、`fact_type`、`source_type`、`source_name`、
`source_record_id`、`entity_ids`、`event_time`、`content` 和 `source_tool`。Merge Node 按
`evidence_id` 去重，Rule Validator 校验证据结构，Answer Node保留可读结论与外部来源URL，不展开内部证据编号。

MySQL 到 Neo4j 同步默认只预览，必须显式增加 `--apply` 才写入：

```bash
python -m scripts.sync_neo4j_research_graph --limit 100
python -m scripts.sync_neo4j_research_graph --limit 100 --batch-id stage9-001 --apply
```

同步使用 `MERGE` 保证同一学者、论文及 `AUTHOR_OF` 关系可重复执行。

配置 `ENTITY_BACKEND=hybrid` 后启用 MySQL + Milvus 双路召回；确定性 Mock 仅用于自动化测试：

```bash
export EMBEDDING_PROVIDER=bge_m3
export ENTITY_BACKEND=hybrid
export HF_HOME=.runtime/huggingface
export EMBEDDING_CACHE_DIR=.runtime/huggingface
export GRAPHRAG_MILVUS_URI=.runtime/milvus-bge-m3.db
python -m scripts.sync_milvus_entities --source mock
python demo.py
```

从 MySQL 使用真实 BGE-M3 重建索引：

```bash
export EMBEDDING_PROVIDER=bge_m3
export EMBEDDING_MODEL_NAME=BAAI/bge-m3
export ENTITY_BACKEND=milvus
python -m scripts.sync_milvus_entities --source mysql --limit 10000
```

### 独立合成数据源

需要在不修改原始 `gkx` 的前提下演练完整图谱构建时，可生成确定性的 `gkx_synthetic`：

```bash
python -m scripts.generate_synthetic_gkx
python -m scripts.validate_synthetic_gkx
python -m scripts.import_synthetic_gkx  # 默认只预览，不连接或写入 MySQL
```

实际导入必须显式指定 `--apply --confirm-database gkx_synthetic`。导入器会拒绝把 `gkx`
作为目标。数据模型、规模参数和安全操作参见 `docs/07_synthetic_gkx_dataset.md`。

真实 BGE-M3 首次运行会下载模型，并使用 1024 维 Dense Vector 与模型 lexical weights。测试显式注入 Mock Embedding，不重复加载模型。

统一 ID 示例：

```text
canonical: person_zw_001
mysql:     450e887j
neo4j:     SCH001
```

`GraphRAGState.resolved_entities` 保存 `person_zw_001`；AchievementService 查询 MySQL 前转换成 `450e887j`，GraphService 查询 Neo4j 前转换成 `SCH001`，结果写回 State 前再转换回 canonical ID。

真实数据模式示例（请在终端或本机未跟踪的 `.env` 中配置密码）：

```bash
export ENTITY_BACKEND=mysql
export ACHIEVEMENT_BACKEND=mysql
export GRAPH_BACKEND=neo4j
export MYSQL_DATABASE=gkx
export MYSQL_PASSWORD='your-local-password'
export NEO4J_PASSWORD='your-local-password'
```

数据调用链：

```mermaid
flowchart LR
    A[Domain Agent] --> T[Domain Tool]
    T --> S{Service Backend Selector}
    S -->|mock| M[Mock Data]
    S -->|mysql| MR[MySQLRepository]
    S -->|neo4j| NR[Neo4jGraphRepository]
    MR --> DB[(gkx MySQL)]
    NR --> G[(Neo4j)]
```

## 运行

要求 Python 3.11+：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python demo.py
pytest -q
uvicorn app.main:app --reload
```

默认`TOOL_TRANSPORT=local`，行为与原有版本一致。需要使用MCP时，先启动工具服务：

```bash
.venv/bin/python -m mcp_runtime.server
```

再在另一个终端启动主应用：

```bash
export TOOL_TRANSPORT=mcp
export MCP_SERVER_URL=http://127.0.0.1:8100/mcp
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

可通过`.venv/bin/python -m scripts.smoke_mcp_tools`列出远端工具。完整边界、白名单、联网搜索配置与故障行为见
[`docs/08_mcp_tool_transport.md`](docs/08_mcp_tool_transport.md)。
Tool契约、可信Skill Loader、评测门禁和多MCP控制面的整体设计见
[`docs/12_tool_skill_mcp_governance.md`](docs/12_tool_skill_mcp_governance.md)。

启动后访问 [http://127.0.0.1:8000](http://127.0.0.1:8000) 打开 GraphRAG Studio 前端。页面支持：

- 从登录页选择系统管理员、科研用户或分析用户；登录后顶栏显示当前账号并可退出；
- 输入自然语言问题并创建独立 `thread_id`；
- 使用“联网搜索”按钮按查询开启或关闭 WebResearchAgent；关闭时后端不会构建联网 Agent 或调用 Tavily/MCP；
- 使用“对话记忆”按钮开启多轮实体指代；“清除记忆”会删除当前会话的轮次和实体焦点，但不会删除知识图谱数据；
- 使用“经验记忆”按钮按查询开启或关闭历史策略召回；结果区展示命中模板、相似度、置信度、样本数、成功率和历史 Agent/Tool 策略；
- 使用顶栏“记忆管理”搜索和维护长期事实；待复核事实可续期或归档，页面展示 revision、召回次数、应用次数和最近召回时间；
- 实时查看 Router、Entity Resolution、Supervisor、Domain Agent、Tool、Merge、Validator、Verification 和 Answer 事件；
- 在检测到同名专家时选择候选 `entity_id`，从 LangGraph interrupt 中断点恢复；
- 查看最终中文答案、规则校验状态、实体 ID 和完整 `GraphRAGState`；
- 使用 SSE 实时接收执行轨迹，不把 Agent Messages 写入 Shared State；彩色节点可查看脱敏后的输入与输出。

前端是 FastAPI 同源静态页面，不需要 Node.js 构建：

```text
frontend/index.html
frontend/styles.css
frontend/app.js
```

### 本地登录账号

首次启动会在 `.runtime/users.sqlite` 创建三个账号。密码只以 PBKDF2-SHA256 哈希存储，浏览器会话使用 HttpOnly Cookie：

| 显示名称 | 用户名 | 初始密码 | `user_id` |
|---|---|---|---|
| 系统管理员 | `admin` | `Admin@123` | `user-admin` |
| 科研用户 | `researcher` | `Research@123` | `user-researcher` |
| 分析用户 | `analyst` | `Analyst@123` | `user-analyst` |

初始密码可通过 `.env` 的 `AUTH_ADMIN_PASSWORD`、`AUTH_RESEARCHER_PASSWORD`、`AUTH_ANALYST_PASSWORD` 修改。它们仅在账号第一次写入数据库时生效，不会在重启时覆盖已有密码。以上默认值仅适合本地开发；对外部署前必须替换，使用 HTTPS，并设置 `AUTH_COOKIE_SECURE=true`。

## 模型配置

默认是 `auto` 模式：检测到智谱 Key 时使用 GLM-5.2，否则回退 Mock：

```bash
export ZHIPUAI_API_KEY=your-api-key
```

也可以显式配置通用变量：

```bash
export MODEL_PROVIDER=openai
export MODEL_NAME=glm-5.2
export MODEL_API_KEY=your-api-key
export MODEL_BASE_URL=https://open.bigmodel.cn/api/paas/v4/
```

每个 Agent 可覆盖 provider、模型、地址、温度、超时、重试和价格。例如：

```bash
export ACHIEVEMENT_AGENT_MODEL_NAME=glm-5.2
export WEB_RESEARCH_AGENT_MODEL_NAME=glm-5.2
export VERIFICATION_AGENT_MODEL_TEMPERATURE=0
```

Harness 运行预算同样支持全局与每 Agent 两级配置：

```bash
export AGENT_MAX_DURATION_SECONDS=120
export AGENT_MAX_TOKENS=64000
export AGENT_MAX_COST=2
export AGENT_TOOL_TIMEOUT_SECONDS=30
export AGENT_TOOL_MAX_RETRIES=1
export AGENT_OBSERVATION_MAX_CHARS=8000
export ACHIEVEMENT_AGENT_MAX_TOKENS=32000
```

`AGENT_MAX_TOKENS=0` 或 `AGENT_MAX_COST=0` 表示不限制。SSE 会推送模型开始/结束、工具重试/超时、Observation 压缩、循环终止和预算更新等细粒度事件。

需要强制离线时使用 `MODEL_PROVIDER=mock`。密钥只通过环境变量注入，禁止写入源码、README 或提交到 Git。

完整模板见 `.env.example`。项目会从根目录自动读取 `.env`，且系统环境变量优先级更高；`.env` 已被 Git 忽略，不会提交。

## 后台 Run 与持久化 API

启动服务：

```bash
uvicorn app.main:app --reload
```

接口：

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/auth/users` | 返回登录页可选用户，不返回密码或哈希 |
| `POST` | `/auth/login` | 校验用户和密码并创建持久化登录会话 |
| `GET` | `/auth/me` | 查询当前登录用户 |
| `POST` | `/auth/logout` | 注销当前会话并清除 Cookie |
| `POST` | `/queries` | 创建后台 Run，立即返回 `202 RUNNING` |
| `POST` | `/queries/{run_id}/resume` | 提交 `{姓名: entity_id}`，后台恢复执行 |
| `POST` | `/queries/{run_id}/cancel` | 协作式取消后台 Run |
| `GET` | `/queries/{run_id}` | 查询运行状态，包括 `ENTITY_NOT_FOUND/CANCELLED/TIMED_OUT` |
| `GET` | `/queries/{run_id}/stream` | SSE 推送 trace 与终态 |
| `GET` | `/queries/{run_id}/history` | 查询 SQLite Checkpoint 历史 |
| `GET` | `/conversations/{conversation_id}/memory` | 查询当前用户拥有的会话轮次和实体焦点 |
| `DELETE` | `/conversations/{conversation_id}/memory` | 清除当前用户拥有的会话记忆，不影响知识图谱 |
| `GET` | `/memory/summary` | 查看当前用户长期记忆摘要 |
| `GET` / `POST` | `/memory/facts` | 搜索或新增当前用户长期事实 |
| `PATCH` / `DELETE` | `/memory/facts/{fact_id}` | 携带 `expected_revision` 修改或删除当前用户拥有的事实 |
| `POST` | `/memory/facts/{fact_id}/review` | 续期或归档到期待复核的事实 |
| `GET` | `/memory/audit` | 查询当前用户的记忆生命周期审计 |
| `GET` | `/memory/export` | 导出当前用户个人记忆 JSON |
| `DELETE` | `/memory` | 二次确认后清除当前用户全部个人记忆 |
| `GET` | `/experience-memory/stats` | 查询当前用户私有及脱敏全局经验统计 |
| `GET` | `/experience-memory/patterns` | 查询当前用户可见的私有及全局历史策略 |
| `GET` | `/queries/{run_id}/events` | 兼容性增量事件接口 |
| `GET` | `/health` | 查看阶段、模型后端和 Checkpointer |
| `GET` | `/health/dependencies` | 主动探测当前启用的 MySQL、Milvus、Neo4j 或 Mock 后端 |
| `GET` | `/skills` | 查看可信仓库Skill的版本、内容哈希、Schema和启停状态 |
| `PATCH` | `/skills/{skill_id}` | 管理员启停Skill；启用前执行离线评测门禁 |
| `GET` | `/metrics` | 查看无敏感数据的运行状态与耗时摘要 |

默认检查点文件是 `.runtime/checkpoints.sqlite`，用户和登录会话文件是 `.runtime/users.sqlite`，都已被 `.gitignore` 排除。
新问题必须使用新的 `run_id`；重复提交相同 ID 返回 `409`。

生产环境通过 `MEMORY_BACKEND=mysql` 将记忆写入独立 `gkx_runtime` 数据库；设置为 `sqlite` 时，会话、经验和长期事实分别使用 `.runtime` 下的三个 SQLite 文件。用户私有作用域记录正负样本；只有存在最终答案、规则校验通过、领域 Tool 无错误且模板通过脱敏检查的 Run 才进入全局作用域。默认至少 5 个样本且置信度达到 0.75 才标记为可应用，但 `Shadow` 模式仍不会直接改变执行路径。升级前没有用户归属字段的旧 SQLite 记忆会保留在不可召回的 `legacy`/`legacy-unowned` 隔离桶中。

## 完整执行流程

```mermaid
flowchart TD
    U[User Query] --> R[Router Node]
    R --> E[Entity Resolution Node]
    E -->|同名| I[interrupt: NEED_USER_SELECTION]
    I -->|Command resume + entity_id| E
    E --> C{Simple / Complex}
    C -->|Simple Talent| TA[Talent Agent Loop]
    C -->|Simple Achievement| AA[Achievement Agent Loop]
    C -->|Simple Enterprise| EA[Enterprise Agent Loop]
    C -->|Simple Industry| IA[Industry Agent Loop]
    C -->|Simple Graph| GA[Graph Reasoning Agent Loop]
    C -->|Complex| S[Supervisor / Planner Node]
    S --> P[Structured Tasks]
    P -->|dynamic parallel fan-out| TA
    P -->|dynamic parallel fan-out| AA
    P -->|dynamic parallel fan-out| IA
    P -->|dynamic parallel fan-out| GA
    TA --> M[Merge Node]
    AA --> M
    EA --> M
    IA --> M
    GA --> M
    M --> V[Rule Validator]
    V -->|规则失败且未达 max_replans| RP[Minimal Replan]
    RP --> S
    V --> Q{requires_verification?}
    Q -->|否| AN[Answer Node]
    Q -->|是| VA[Verification Agent]
    VA --> VE[verify_evidence]
    VE --> CS[check_source]
    CS --> TL[get_cooperation_timeline]
    TL --> REL[validate_relation]
    REL --> CC[check_constraints]
    CC --> VF{PASS / FAIL}
    VF -->|PASS| AN
    VF -->|FAIL 且证据不足且未达 max_replans| RP
    VF -->|FAIL 但证据充分或达到上限| AN
    AN --> O[中文答案]
```

每个 Domain Agent Loop 内部执行：

```mermaid
flowchart LR
    SM[System + Human Messages] --> LLM[Mock or Real ChatModel]
    LLM -->|tool_calls| T[Authorized Tool]
    T --> TM[ToolMessage Observation]
    TM --> LLM
    LLM -->|no tool_calls| DR[DomainResult]
    LLM -->|max_steps| ERR[Controlled Error]
```

Supervisor 只为 Query 涉及的领域创建任务。例如“综合分析学术、职业、企业、产业链和间接关系路径”可并行调用五个领域 Agent；当计划为 `sequential` 时逐个执行，`parallel` 计划也会等待 `depends_on` 完成后再启动下游任务。简单企业或产业链问题会跳过 Supervisor，直接进入相应 Agent。

语义判断场景“判断张伟和李明是不是长期稳定的核心科研合作伙伴”执行：

```text
Router → Entity Resolution → AchievementAgent
→ 共同论文 + 共同项目 + 聚合结果
→ Merge → Rule Validator
→ VerificationAgent 多轮 Tool Calling
→ Evidence / Source / Timeline / Relation / Constraints
→ PASS 或 FAIL → Answer 或受 max_replans 限制的 Replan
```

Rule Validator 使用普通 Python 校验 entity_id、共同作者/项目参与者、evidence_id、项目时间范围、聚合 count 和数据完整性。VerificationAgent 不替代这些规则，只负责“长期、稳定、核心合作伙伴”这样的语义关系判断。

## 测试

当前测试覆盖：

- 重名实体暂停与恢复；
- 简单论文查询跳过 Supervisor；
- 企业角色、项目、专利工具；
- 产业链结构、节点企业、TOP-N 事件；
- 一跳邻居、最短路径、K 跳扩展、路径强度；
- 简单企业路由；
- 无人物实体的产业链查询；
- 五领域复杂 Query 的 Supervisor 动态并行 fan-out 与 Merge。
- Rule Validator 的时间范围、count 和数据完整性检查；
- VerificationAgent 五步 Tool Calling/ToolMessage 循环；
- 长期稳定核心科研合作伙伴 PASS 场景；
- Verification FAIL 与 Rule Validation FAIL 的 `max_replans` 路由限制。
- 所有 Domain Agent 的多轮 ToolMessage 回灌与停止条件；
- OpenAI Provider 缺少 API Key 时的快速失败；
- Supervisor 只补调缺失领域的最小化 Replan；
- FastAPI 创建、interrupt、resume、State 和 history；
- SQLite Checkpointer 的跨请求会话持久化。
- Harness Middleware 顺序、循环检测、Token 预算、Observation 压缩；
- Tool 瞬态错误重试、超时和错误分类注入；
- sequential、parallel 依赖波次、未知依赖与依赖环调度；
- 后台 Run 状态、SSE 终态和重复 `run_id` 拒绝；
- Supervisor 任务契约与 Rule Validator 验收；
- Verification 经 Service/Repository 使用当前数据后端；
- 节点快照敏感字段脱敏和 Graph path strength 答案。
- MySQL + Milvus 候选融合、上下文重排和自动确认阈值；
- 持久化 Run Registry、协作式超时以及统一 Evidence Repository。
- MySQL 任职、教育、项目和专利参数化查询，以及专利/教育端到端回答。
- 统一 EvidenceRecord、Neo4j 企业/产业 Service 适配和依赖健康探针；
- 可重复的 MySQL→Neo4j 同步预览/写入流程；
- 路由、工具、答案、校验和证据覆盖端到端评测。
- API → LangGraph → Agent → MCP → 数据库/模型的统一 Trace；
- 模型 Token/成本、工具成功率、重规划、超时率和 P95 延迟统计；
- 50 条分层黄金集、CI 回归门禁和前端双 Run 对比。

运行：

```bash
pytest -q
python -m scripts.evaluate_stage9_e2e
python -m scripts.run_agent_evals --check
python -m scripts.run_harness_evals --check
# 配置真实 MODEL_PROVIDER/API Key 后运行小样本重复评测
python -m scripts.run_live_agent_evals --limit 10 --repeats 2
```

当前测试结果以本机 `pytest -q` 输出为准；真实数据库集成采用单独 smoke test，不进入默认 CI。

## Agent 评测与可观测

每次 API 查询都生成持久化 Trace，并记录 API、LangGraph Node、Agent、模型、Tool、MCP Client/Server
以及 MySQL、Neo4j、Milvus Span。接口包括：

```text
GET /observability/summary
GET /observability/runs
GET /observability/runs/{run_id}
GET /observability/compare?left_run_id=...&right_run_id=...
```

在 `.env` 中设置模型的每百万 Token 单价后，系统按真实 Provider 返回的 usage 计算单次 Run 成本；
Mock 模型没有真实 Token，成本固定为 0。浏览器页面底部可以选择两个 Run，对比模型、Prompt、工作流版本、
总耗时、Token、成本、工具成功率、重规划、错误数和最慢 Span。

`evals/golden_v1.jsonl` 固定包含 10 条实体消歧、20 条路由、20 条完整工作流用例。
CI 使用 `evals/baselines/agentops_v1.json` 同时执行绝对门槛与相对回退检测，失败时阻止合并，并上传评测报告。
`evals/harness_fault_cases.json` 独立评测 Harness Middleware、重复循环、瞬态故障恢复、超时分类、Observation 压缩和 Token 预算，避免改变业务黄金集的固定规模。
`evals/expert_report_cases.json` 独立评测专家报告的完整/简版规划、引用有效性、输入保护和可选域故障降级。
`evals/industry_landscape_cases.json` 独立评测产业全景报告的能力规划、引用有效性、输入保护和联网故障降级。
`scripts.run_live_agent_evals` 使用真实模型重复运行小样本，报告路由/工作流稳定性、P95 延迟、非法 Tool、
Agent 不完整结束、无进展停止和平均 Replan；默认拒绝 `MODEL_PROVIDER=mock`，不替代确定性 CI 基线。
详细设计见 [docs/07_agent_evaluation_observability.md](docs/07_agent_evaluation_observability.md)。

专家报告 Skill 的边界、调用协议和评测见 [docs/09_expert_report_skill.md](docs/09_expert_report_skill.md)。

产业全景报告 Skill 的边界、证据规则和评测见 [docs/10_industry_landscape_skill.md](docs/10_industry_landscape_skill.md)。

四层记忆架构、分阶段迁移边界和验收状态见 [docs/11_memory_architecture.md](docs/11_memory_architecture.md)。

九项 Agent 运行时优化（任务实例、Profile、检索计划、完成门禁、验证策略、并发、韧性、经验辅助和真实模型评测）
见 [docs/13_agent_runtime_optimization.md](docs/13_agent_runtime_optimization.md)。

## 后续阶段建议

后续可增加用户管理后台、细粒度 Run/记忆访问授权、Redis/Celery 或同类跨进程任务队列，并将当前 vendor-neutral Trace 导出为
OpenTelemetry/OTLP，接入 Grafana Tempo、Jaeger 或 LangSmith。

第七阶段运行时和任务契约详解见 [docs/03_stage7_runtime.md](docs/03_stage7_runtime.md)。

当前项目从 API、LangGraph、实体消歧、Agent Tool Calling 到 SSE 返回的完整流程见 [docs/04_current_runtime_flow.md](docs/04_current_runtime_flow.md)。

真实后端启动后可执行在线查询黑盒验收：

```bash
python -m scripts.smoke_online_query \
  --base-url http://127.0.0.1:8000 \
  --question '南京科技大学042的何伟发表过哪些论文？'

# 对重名问题自动选择首个候选，覆盖 interrupt/resume 分支
python -m scripts.smoke_online_query \
  --question '何伟发表过哪些论文？' --auto-select-first
```

第八阶段混合实体解析、统一证据与 Run 治理见 [docs/05_stage8_hybrid_resolution.md](docs/05_stage8_hybrid_resolution.md)。

第九阶段真实数据、统一证据和评测见 [docs/06_stage9_real_data_and_evaluation.md](docs/06_stage9_real_data_and_evaluation.md)。
