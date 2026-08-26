"""把远端 MCP Tool 动态适配成 LangChain StructuredTool。"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Any

from langchain_core.tools import StructuredTool
from mcp import Client
from services.telemetry import trace_carrier, traced_span


def _run_sync(factory: Callable[[], Awaitable[Any]]) -> Any:
    """在同步 LangGraph Node 中调用异步 MCP Client，也兼容已有事件循环的调用方。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="mcp-sync-bridge") as executor:
        return executor.submit(lambda: asyncio.run(factory())).result()


class MCPGateway:
    def __init__(self, target: Any, timeout_seconds: float = 30):
        self.target = target
        self.timeout_seconds = timeout_seconds

    async def list_tool_specs_async(self) -> list[dict]:
        async with Client(self.target, read_timeout_seconds=self.timeout_seconds) as client:
            result = await client.list_tools()
            return [item.model_dump() for item in result.tools]

    def list_tool_specs(self) -> list[dict]:
        return _run_sync(self.list_tool_specs_async)

    async def call_tool_async(self, name: str, arguments: dict[str, Any]) -> Any:
        with traced_span(f"mcp.client.{name}", "mcp_client", {
            "mcp.tool.name": name,
            "mcp.server": str(self.target),
        }):
            carrier = trace_carrier()
            meta = {"graphrag_trace": carrier} if carrier else None
            async with Client(self.target, read_timeout_seconds=self.timeout_seconds) as client:
                result = await client.call_tool(name, arguments, read_timeout_seconds=self.timeout_seconds,
                                                meta=meta)
        if result.is_error:
            raise RuntimeError(f"MCP工具执行失败 {name}: {self._content_text(result.content)}")
        if result.structured_content is not None:
            payload = result.structured_content
            return payload.get("result") if isinstance(payload, dict) and set(payload) == {"result"} else payload
        text = self._content_text(result.content).strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        return _run_sync(lambda: self.call_tool_async(name, arguments))

    @staticmethod
    def _content_text(content: list[Any]) -> str:
        return "\n".join(str(getattr(item, "text", "")) for item in content if getattr(item, "text", None))


@lru_cache(maxsize=8)
def discover_http_tools(url: str, timeout_seconds: float) -> tuple[dict, ...]:
    """工具Schema在进程内缓存；每次工具执行仍使用独立、可回收的MCP连接。"""
    return tuple(MCPGateway(url, timeout_seconds).list_tool_specs())


def build_langchain_mcp_tools(target: Any, allowed_names: list[str], timeout_seconds: float = 30,
                              use_discovery_cache: bool = True) -> list[StructuredTool]:
    gateway = MCPGateway(target, timeout_seconds)
    try:
        if isinstance(target, str) and use_discovery_cache:
            specs = list(discover_http_tools(target, timeout_seconds))
        else:
            specs = gateway.list_tool_specs()
    except Exception as exc:
        error_type, message = _exception_details(exc)
        raise RuntimeError(f"无法连接MCP Server {target}: {error_type}: {message}") from exc
    by_name = {item["name"]: item for item in specs}
    missing = [name for name in allowed_names if name not in by_name]
    if missing:
        raise RuntimeError(f"MCP Server缺少授权工具: {', '.join(missing)}")

    result = []
    for tool_name in allowed_names:
        spec = by_name[tool_name]

        def sync_call(_name=tool_name, **arguments):
            return gateway.call_tool(_name, arguments)

        async def async_call(_name=tool_name, **arguments):
            return await gateway.call_tool_async(_name, arguments)

        result.append(StructuredTool(
            name=tool_name,
            description=spec.get("description") or tool_name,
            args_schema=spec["input_schema"],
            func=sync_call,
            coroutine=async_call,
            metadata={"tool_transport": "mcp", "mcp_server": str(target)},
        ))
    return result


def mcp_server_health(url: str, timeout_seconds: float = 30) -> dict:
    try:
        specs = MCPGateway(url, timeout_seconds).list_tool_specs()
        domains = sorted({(item.get("meta") or {}).get("domain", "unknown") for item in specs})
        return {"ready": True, "url": url, "tool_count": len(specs), "domains": domains}
    except Exception as exc:  # noqa: BLE001 - 健康探针将协议/网络异常转换为degraded状态
        error_type, message = _exception_details(exc)
        return {"ready": False, "url": url, "error_type": error_type, "message": message}


def _exception_details(exc: BaseException) -> tuple[str, str]:
    current = exc
    while isinstance(current, BaseExceptionGroup) and current.exceptions:
        current = current.exceptions[0]
    return type(current).__name__, str(current)
