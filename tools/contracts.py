"""Tool、Agent、Capability 与 FactType 的统一运行时契约。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ToolTrustLevel = Literal["internal", "remote_content"]
ToolTransport = Literal["local", "mcp"]


@dataclass(frozen=True)
class ToolSpec:
    """一个可执行 Tool 的稳定身份、授权边界与验收元数据。"""

    name: str
    domain: str
    agent: str
    implementation: Any
    fact_types: tuple[str, ...]
    capabilities: tuple[str, ...] = ()
    trust_level: ToolTrustLevel = "internal"
    default_transport: ToolTransport = "local"
    timeout_seconds: float = 30.0
    idempotent: bool = True
    open_world: bool = False


@dataclass(frozen=True)
class AgentContract:
    """领域 Agent 的工具白名单与普通查询默认验收事实。"""

    agent: str
    domain: str
    default_required_fact_types: tuple[str, ...]


@dataclass(frozen=True)
class CapabilitySpec:
    """Skill 声明的业务能力；由 Supervisor 展开为领域 Agent 任务。"""

    name: str
    agent: str
    domain: str
    goal: str
    required_fact_types: tuple[str, ...]
