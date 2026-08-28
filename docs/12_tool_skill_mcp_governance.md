# Tool、Skill、MCP 分阶段治理

## 第一阶段：Tool 契约

`ToolRegistry`是Capability、Agent、Domain、Tool和FactType映射的唯一事实源。33个现有领域Tool均注册为
`ToolSpec`，Agent从Registry取得自己的白名单，默认执行通道保持Local。契约校验会拒绝重复Tool、
Agent/Domain不一致、未知FactType，以及Capability跨Agent引用FactType。

主要入口：

- `tools/contracts.py`：`ToolSpec`、`AgentContract`、`CapabilitySpec`；
- `tools/registry.py`：统一注册与契约校验；
- `tools/provider.py`：只负责按控制面选择Local或MCP实现。

## 第二阶段：Skill 规范

两个仓库内Skill使用YAML Frontmatter声明稳定ID、版本、触发词、Capability、输入/输出Pydantic Schema和
评测策略。Loader只扫描`skills/*/SKILL.md`，引用仅允许`skills.*`与`evaluation.*`模块；第一版没有上传、
安装或执行任意Python文件的API。

每次加载记录`skill_id + version + content_hash`。Router把三项写入State和事件。Skill输出在进入最终
答案前按输出Schema校验。管理员可通过`PATCH /skills/{skill_id}`启停；从停用切换到启用时必须先通过
声明的离线评测门禁。也可在CI执行：

```bash
.venv/bin/python -m scripts.validate_runtime_skills
```

## 第三阶段：MCP 控制面

MCP只改变Tool发现和调用通道，不接管Router、Supervisor、Validator或LangGraph。支持多个Server、
逐Server领域/Tool白名单、逐领域Local/MCP混合传输、工具名前缀和来源metadata。配置了Web领域Server后，
`WebResearchAgent`优先试用外部MCP，其他Agent仍可保持Local。

本项目的`mcp_runtime.server`继续对外提供全部33个规范Tool。远程结果先清洗再进入模型，并为每次调用
生成不含原文的Tool Receipt，便于审计与问题定位。

## 高级图查询契约

GraphReasoningAgent新增五个只读Tool：

| Tool | 用途 | 强制上限 |
|---|---|---|
| `get_neighbors_filtered` | 按关系、方向、目标Label、时间、权重和属性筛选一跳邻居 | 100行、20种关系、10个过滤条件 |
| `find_paths` | 返回Top-K最短或高权重简单路径 | 6跳、Top 10 |
| `query_subgraph` | 围绕种子实体返回节点和关系 | 3跳、200节点、500条边 |
| `aggregate_graph` | 计数、去重、分组、排序及数值聚合 | 100行、5个分组字段、10个指标 |
| `get_graph_schema` | 向Planner公开允许查询的Label、关系、属性和上限 | 只读、带版本和内容哈希 |

Agent不能提交原始Cypher。`models/graph_queries.py`定义受限Pydantic DSL，
`services/graph_schema.py`定义治理白名单；Neo4j Repository只把通过白名单的结构名编译进Cypher，
所有实体ID、过滤值和Limit均使用参数。Mock与Neo4j实现返回同一结果契约。

## 验证入口

```bash
.venv/bin/pytest -q tests/test_tool_registry.py
.venv/bin/pytest -q tests/test_skill_loader.py
.venv/bin/pytest -q tests/test_mcp_tools.py tests/test_agent_harness.py
.venv/bin/pytest -q tests/test_advanced_graph_tools.py
```
