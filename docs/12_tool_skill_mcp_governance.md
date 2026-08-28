# Tool、Skill、MCP 分阶段治理

## 第一阶段：Tool 契约

`ToolRegistry`是Capability、Agent、Domain、Tool和FactType映射的唯一事实源。28个现有领域Tool均注册为
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

本项目的`mcp_runtime.server`继续对外提供全部28个规范Tool。远程结果先清洗再进入模型，并为每次调用
生成不含原文的Tool Receipt，便于审计与问题定位。

## 验证入口

```bash
.venv/bin/pytest -q tests/test_tool_registry.py
.venv/bin/pytest -q tests/test_skill_loader.py
.venv/bin/pytest -q tests/test_mcp_tools.py tests/test_agent_harness.py
```
