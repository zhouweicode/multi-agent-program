"""MCP Server 控制面：白名单、领域路由和可见工具前缀。"""

from __future__ import annotations

from dataclasses import dataclass

from mcp_runtime.config import MCPServerConfig
from tools.registry import ToolRegistry


@dataclass(frozen=True)
class MCPToolBinding:
    server: MCPServerConfig
    canonical_names: tuple[str, ...]


class MCPServerRegistry:
    def __init__(
        self,
        servers: tuple[MCPServerConfig, ...],
        tool_registry: ToolRegistry,
    ) -> None:
        if len({item.name for item in servers}) != len(servers):
            raise ValueError("MCP Server name 必须唯一")
        self._servers = tuple(item for item in servers if item.enabled)
        self._tools = tool_registry
        known_domains = set(tool_registry.agent_domains.values())
        known_tools = {item.name for item in tool_registry.list()}
        for server in self._servers:
            unknown_domains = set(server.domains) - known_domains
            unknown_tools = set(server.allowed_tools) - known_tools
            if unknown_domains:
                raise ValueError(
                    f"MCP Server {server.name} 声明未知领域: {', '.join(sorted(unknown_domains))}"
                )
            if unknown_tools:
                raise ValueError(
                    f"MCP Server {server.name} 白名单包含未知 Tool: {', '.join(sorted(unknown_tools))}"
                )

    def bindings_for(self, domain: str) -> list[MCPToolBinding]:
        required = self._tools.tool_names(domain)
        owners: dict[str, MCPServerConfig] = {}
        for server in self._servers:
            if server.domains and domain not in server.domains:
                continue
            allowed = set(server.allowed_tools)
            names = (
                required
                if not allowed
                else [name for name in required if name in allowed]
            )
            for name in names:
                if name in owners:
                    raise ValueError(
                        f"Tool {name} 同时由 MCP Server {owners[name].name} 和 {server.name} 提供"
                    )
                owners[name] = server
        missing = [name for name in required if name not in owners]
        if missing:
            raise ValueError(
                f"MCP 控制面未给领域 {domain} 分配全部授权工具: {', '.join(missing)}"
            )
        return [
            MCPToolBinding(
                server, tuple(name for name in required if owners[name] is server)
            )
            for server in self._servers
            if any(owners.get(name) is server for name in required)
        ]

    def list(self) -> list[MCPServerConfig]:
        return list(self._servers)
