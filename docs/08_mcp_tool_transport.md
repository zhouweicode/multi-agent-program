# MCP Tool传输与多Server控制面

## 实现边界

MCP只替换可复用Tool的发现和调用通道，不替换LangGraph，也不把系统控制权交给远端服务。

保留在主应用内的组件：Router、Entity Resolution、Supervisor、Merge、Rule Validator、Replan、Answer、
Checkpoint和Evidence Normalizer。

MCP Server对外提供七组能力：

| 工具组 | 能力 | 当前Agent授权 |
|---|---|---|
| `talent` | 画像、任职、教育和任职重叠 | TalentAgent |
| `achievement` | 论文、项目、专利和合作聚合 | AchievementAgent |
| `enterprise` | 企业角色、企业项目和企业专利 | EnterpriseAgent |
| `industry` | 产业节点、企业和事件 | IndustryAgent |
| `graph` | 邻居过滤、Top-K路径、K跳、受限子图、聚合、Schema和路径强度 | GraphReasoningAgent |
| `verification` | 证据、来源、时间线、关系和约束验证 | VerificationAgent |
| `web` | Brave/Tavily公开网页搜索 | `WebResearchAgent` |

每个Agent只从MCP Server加载其白名单。`WebResearchAgent`只获得`search_web`，该工具不会因为Server公开了它就自动出现在其他Agent上下文中。

## 调用链

local模式：

```text
LLM tool_calls -> LangChain Tool -> Service -> Repository -> Database
```

MCP模式：

```text
LLM tool_calls -> LangChain MCP Adapter -> Streamable HTTP -> MCP Server
               -> Local Tool -> Service -> Repository -> Database
```

两种模式使用相同的规范工具名、JSON Schema、返回结构、证据归一化和Agent调用预算。
MCP工具暴露给模型时可增加`<tool_prefix>__<canonical_name>`前缀；进入Fact、Validator和
Evidence Normalizer前会还原为规范工具名。

## 多Server与混合传输

默认仍为全Local。可只让`WebResearchAgent`走外部MCP，其余领域继续Local：

```env
TOOL_TRANSPORT=local
MCP_SERVERS_JSON={"public_web":{"url":"https://mcp.example.com/mcp","domains":["web"],"allowed_tools":["search_web"],"tool_prefix":"external"}}
```

显式配置了负责`web`领域的Server后，`WebResearchAgent`会优先使用它。以下配置可精确覆盖自动选择：

```env
TOOL_TRANSPORT_OVERRIDES_JSON={"web":"mcp","talent":"local"}
```

控制面规则：

- 只有`MCP_SERVERS_JSON`登记且`enabled=true`的Server可用；
- `domains`是Server白名单，`allowed_tools`是Tool白名单；
- 一个领域白名单内的每个Tool必须恰好分配给一个Server，缺失或重复都在Agent创建前失败；
- `tool_prefix`用于隔离不同来源，来源同时写入Tool metadata和Tool Receipt；
- 兼容旧配置：未声明`MCP_SERVERS_JSON`时，`MCP_SERVER_URL`仍作为`default` Server。

## 启动方式

local模式只需启动主应用：

```bash
export TOOL_TRANSPORT=local
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

MCP模式先启动工具服务：

```bash
.venv/bin/python -m mcp_runtime.server
```

再启动主应用：

```bash
export TOOL_TRANSPORT=mcp
export MCP_SERVER_URL=http://127.0.0.1:8100/mcp
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

MCP Server使用官方推荐的Streamable HTTP。默认只监听`127.0.0.1:8100`；当前实现没有公网OAuth，禁止把监听地址改成公网地址。

## 冒烟验证

列出所有MCP工具：

```bash
.venv/bin/python -m scripts.smoke_mcp_tools
```

调用一个工具：

```bash
.venv/bin/python -m scripts.smoke_mcp_tools \
  --tool get_person_profile \
  --arguments '{"entity_id":"person_zw_001"}'
```

主应用的`GET /health`会展示逐领域`tool_transports`和无密钥Server元数据；
`GET /health/dependencies`会分别探测当前实际使用的MCP Server。

## 联网搜索

默认关闭，关闭时调用`search_web`返回结构化`WEB_SEARCH_NOT_CONFIGURED`，不会让工作流异常退出。

Brave示例：

```env
WEB_SEARCH_PROVIDER=brave
WEB_SEARCH_API_KEY=your-key
```

Tavily示例：

```env
WEB_SEARCH_PROVIDER=tavily
WEB_SEARCH_API_KEY=your-key
```

工具只接受查询、结果数量、时间范围和域名过滤，不允许模型传入任意Endpoint或认证头。网页摘要属于外部证据候选，必须经过来源验证和冲突检测后才能写入图谱。

所有MCP结果及`remote_content`结果在进入模型上下文前都会递归清除伪造角色标签和不可见控制字符。
每次真实Tool调用都会生成`Tool Receipt`，记录规范名、可见名、Server来源、输入/输出SHA-256、
输出字节数、状态和清洗统计，但不保存参数或结果原文。领域结果通过`tool_receipts`返回这些回执。

前端“联网搜索”按钮通过每次查询的`web_search_enabled`字段控制WebResearchAgent。关闭时，
纯联网问题会返回明确提示；混合问题继续执行知识图谱领域任务，但不会构建WebResearchAgent或发起外部请求。

## 故障行为

- `TOOL_TRANSPORT=mcp`但Server不可用：Agent加载工具时明确失败，不静默回退local。
- Server缺少白名单中的工具：在模型执行前报出缺失工具名。
- Tool返回MCP错误：清洗后转成当前Agent的`errors`和`ToolMessage`，并生成失败回执，由现有Replan/Answer链路处理。
- MCP工具Schema按Server URL在主应用进程缓存，具体调用使用独立连接，避免跨线程复用异步Session。

生产部署应继续增加OAuth/mTLS、租户上下文、限流、熔断和OpenTelemetry链路追踪。
