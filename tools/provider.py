"""Agent Tool 白名单与 local/mcp 双传输选择。"""

from __future__ import annotations

from typing import Any

from mcp_runtime.client import build_langchain_mcp_tools
from mcp_runtime.registry import MCPServerRegistry
from models.settings import Settings
from tools.registry import tool_registry

# 兼容已有调试与测试代码；真实来源只有 ToolRegistry。
LOCAL_TOOL_GROUPS: dict[str, tuple[Any, ...]] = {
    domain: tuple(item.implementation for item in tool_registry.list(domain))
    for domain in tool_registry.agent_domains.values()
}


def tool_names(group: str) -> list[str]:
    return tool_registry.tool_names(group)


def get_tools(
    group: str,
    settings: Settings | None = None,
    mcp_target: Any | None = None,
    use_discovery_cache: bool = True,
) -> list[Any]:
    settings = settings or Settings.from_env()
    local = tool_registry.local_tools(group)
    transport = settings.tool_transport_for(group)
    if transport == "local":
        return local
    if transport != "mcp":
        raise ValueError("TOOL_TRANSPORT 只能是 local 或 mcp")
    if mcp_target is not None:
        return build_langchain_mcp_tools(
            mcp_target,
            tool_registry.tool_names(group),
            settings.mcp_request_timeout,
            use_discovery_cache=use_discovery_cache,
            registry=tool_registry,
        )
    control_plane = MCPServerRegistry(settings.resolved_mcp_servers(), tool_registry)
    tools: list[Any] = []
    for binding in control_plane.bindings_for(group):
        tools.extend(
            build_langchain_mcp_tools(
                binding.server.target,
                list(binding.canonical_names),
                settings.mcp_request_timeout,
                use_discovery_cache=use_discovery_cache,
                registry=tool_registry,
                server_name=binding.server.name,
                name_prefix=binding.server.tool_prefix,
            )
        )
    visible_names = [item.name for item in tools]
    if len(visible_names) != len(set(visible_names)):
        raise ValueError(f"领域 {group} 的 MCP 可见工具名冲突")
    return tools
