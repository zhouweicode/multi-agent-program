"""多 MCP Server 的可信配置模型与环境变量解析。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    target: Any
    enabled: bool = True
    allowed_tools: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    tool_prefix: str = ""

    def __post_init__(self) -> None:
        if not _NAME.fullmatch(self.name):
            raise ValueError(f"非法 MCP Server 名称: {self.name}")
        if self.tool_prefix and not _NAME.fullmatch(self.tool_prefix):
            raise ValueError(f"非法 MCP Tool 前缀: {self.tool_prefix}")
        if not self.target:
            raise ValueError(f"MCP Server {self.name} 缺少 target/url")
        if len(set(self.allowed_tools)) != len(self.allowed_tools):
            raise ValueError(f"MCP Server {self.name} allowed_tools 包含重复项")

    @classmethod
    def from_mapping(cls, name: str, value: dict[str, Any]) -> MCPServerConfig:
        if not isinstance(value, dict):
            raise TypeError(f"MCP Server {name} 配置必须是对象")
        allowed = value.get("allowed_tools", [])
        domains = value.get("domains", [])
        for field_name, items in (("allowed_tools", allowed), ("domains", domains)):
            if not isinstance(items, list) or any(
                not isinstance(item, str) or not item for item in items
            ):
                raise ValueError(f"MCP Server {name} 的 {field_name} 必须是字符串数组")
        return cls(
            name=name,
            target=value.get("url") or value.get("target"),
            enabled=bool(value.get("enabled", True)),
            allowed_tools=tuple(allowed),
            domains=tuple(domains),
            tool_prefix=str(value.get("tool_prefix") or ""),
        )


def parse_mcp_servers(raw: str | None) -> tuple[MCPServerConfig, ...]:
    if not raw:
        return ()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise TypeError("MCP_SERVERS_JSON 必须是 Server 名到配置的对象")
    return tuple(
        MCPServerConfig.from_mapping(name, item) for name, item in value.items()
    )


def parse_transport_overrides(raw: str | None) -> tuple[tuple[str, str], ...]:
    if not raw:
        return ()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise TypeError("TOOL_TRANSPORT_OVERRIDES_JSON 必须是领域到传输的对象")
    result = []
    for domain, transport in value.items():
        if not isinstance(domain, str) or transport not in {"local", "mcp"}:
            raise ValueError("领域 Tool 传输只能是 local 或 mcp")
        result.append((domain, transport))
    return tuple(result)
