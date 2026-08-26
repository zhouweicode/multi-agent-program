# MCP Tool传输与local/mcp双模式

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
| `graph` | 邻居、路径、K跳和路径强度 | GraphReasoningAgent |
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

两种模式使用相同工具名、JSON Schema、返回结构、证据归一化和Agent调用预算。

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

主应用的`GET /health`会展示`tool_transport`；MCP模式下`GET /health/dependencies`会探测MCP Server并返回工具数量。

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

前端“联网搜索”按钮通过每次查询的`web_search_enabled`字段控制WebResearchAgent。关闭时，
纯联网问题会返回明确提示；混合问题继续执行知识图谱领域任务，但不会构建WebResearchAgent或发起外部请求。

## 故障行为

- `TOOL_TRANSPORT=mcp`但Server不可用：Agent加载工具时明确失败，不静默回退local。
- Server缺少白名单中的工具：在模型执行前报出缺失工具名。
- Tool返回MCP错误：转成当前Agent的`errors`和`ToolMessage`，由现有Replan/Answer链路处理。
- MCP工具Schema按Server URL在主应用进程缓存，具体调用使用独立连接，避免跨线程复用异步Session。

生产部署应继续增加OAuth/mTLS、租户上下文、限流、熔断和OpenTelemetry链路追踪。
