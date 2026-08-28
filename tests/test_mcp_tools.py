"""MCP Server、LangChain适配和local/mcp双模式契约测试。"""

from mcp import Client

from mcp_runtime.client import build_langchain_mcp_tools
from mcp_runtime.config import (
    MCPServerConfig,
    parse_mcp_servers,
    parse_transport_overrides,
)
from mcp_runtime.registry import MCPServerRegistry
from mcp_runtime.server import mcp
from models.settings import Settings
from services.web_search_service import WebSearchService
from tools.provider import get_tools, tool_names
from tools.registry import tool_registry


def test_mcp_server_exposes_reusable_capabilities():
    async def inspect_server():
        async with Client(mcp) as client:
            result = await client.list_tools()
            return {item.name: item for item in result.tools}

    import asyncio

    tools = asyncio.run(inspect_server())
    assert len(tools) == 33
    assert {
        "get_person_profile",
        "get_author_papers",
        "find_path",
        "find_paths",
        "get_graph_schema",
        "verify_evidence",
        "search_web",
    } <= set(tools)
    assert tools["find_path"].meta["domain"] == "graph"
    assert tools["get_graph_schema"].meta == {
        "domain": "graph",
        "control_plane": True,
    }
    assert tools["search_web"].meta == {"domain": "web", "open_world": True}


def test_mcp_langchain_adapter_preserves_schema_and_result():
    remote = build_langchain_mcp_tools(
        mcp, ["get_person_profile", "find_path"], use_discovery_cache=False
    )
    assert [item.name for item in remote] == ["get_person_profile", "find_path"]
    assert "entity_id" in remote[0].args
    assert remote[0].metadata["tool_transport"] == "mcp"
    assert remote[0].invoke({"entity_id": "person_zw_001"})["name"] == "张伟"


def test_mcp_adapter_preserves_nested_graph_query_schema(monkeypatch):
    monkeypatch.setenv("GRAPH_BACKEND", "mock")
    remote = build_langchain_mcp_tools(
        mcp, ["find_paths", "aggregate_graph"], use_discovery_cache=False
    )
    assert "top_k" in remote[0].args
    assert remote[0].invoke(
        {
            "source_id": "person_zw_001",
            "target_id": "node_model",
            "top_k": 2,
        }
    )["path_count"] == 2
    aggregate = remote[1].invoke(
        {
            "source_label": "Scholar",
            "metrics": [{"operation": "count", "alias": "scholar_count"}],
        }
    )
    assert aggregate["rows"] == [{"scholar_count": 2}]


def test_tool_provider_switches_transport_without_expanding_agent_allowlist():
    local = get_tools("talent", Settings(tool_transport="local"))
    remote = get_tools(
        "talent",
        Settings(tool_transport="mcp"),
        mcp_target=mcp,
        use_discovery_cache=False,
    )
    assert [item.name for item in local] == tool_names("talent")
    assert [item.name for item in remote] == tool_names("talent")
    assert "search_web" not in [item.name for item in remote]
    args = {"entity_id": "person_zw_001"}
    assert remote[0].invoke(args) == local[0].invoke(args)


def test_multi_server_control_plane_routes_by_tool_whitelist_and_prefix():
    servers = (
        MCPServerConfig(
            name="knowledge",
            target=mcp,
            allowed_tools=tuple(tool_names("talent")),
            domains=("talent",),
            tool_prefix="kg",
        ),
        MCPServerConfig(
            name="public_web",
            target=mcp,
            allowed_tools=("search_web",),
            domains=("web",),
            tool_prefix="external",
        ),
    )
    settings = Settings(
        tool_transport="local",
        tool_transport_overrides=(("talent", "mcp"),),
        mcp_servers=servers,
    )
    talent = get_tools("talent", settings, use_discovery_cache=False)
    web = get_tools("web", settings, use_discovery_cache=False)

    assert [item.name for item in talent] == [
        f"kg__{name}" for name in tool_names("talent")
    ]
    assert [item.name for item in web] == ["external__search_web"]
    assert web[0].metadata["canonical_tool_name"] == "search_web"
    assert web[0].metadata["mcp_server_name"] == "public_web"
    assert web[0].metadata["tool_source"] == "mcp:public_web"
    assert settings.tool_transport_for("web") == "mcp"
    assert settings.tool_transport_for("industry") == "local"


def test_mcp_control_plane_rejects_missing_or_duplicate_tool_owners():
    incomplete = MCPServerRegistry(
        (
            MCPServerConfig(
                name="partial",
                target=mcp,
                allowed_tools=("get_person_profile",),
                domains=("talent",),
            ),
        ),
        tool_registry,
    )
    import pytest

    with pytest.raises(ValueError, match="未给领域 talent 分配全部授权工具"):
        incomplete.bindings_for("talent")

    duplicate = MCPServerRegistry(
        (
            MCPServerConfig(name="a", target=mcp, domains=("web",)),
            MCPServerConfig(name="b", target=mcp, domains=("web",)),
        ),
        tool_registry,
    )
    with pytest.raises(ValueError, match="同时由 MCP Server"):
        duplicate.bindings_for("web")


def test_mcp_environment_json_is_strictly_parsed():
    servers = parse_mcp_servers(
        '{"web":{"url":"https://mcp.example.test","domains":["web"],'
        '"allowed_tools":["search_web"],"tool_prefix":"external"}}'
    )
    assert servers[0].name == "web"
    assert servers[0].allowed_tools == ("search_web",)
    assert parse_transport_overrides('{"web":"mcp","talent":"local"}') == (
        ("web", "mcp"),
        ("talent", "local"),
    )


def test_web_search_is_a_safe_structured_error_when_not_configured():
    remote = build_langchain_mcp_tools(mcp, ["search_web"], use_discovery_cache=False)[
        0
    ]
    result = remote.invoke({"query": "GraphRAG"})
    assert result == {
        "error": "WEB_SEARCH_NOT_CONFIGURED",
        "query": "GraphRAG",
        "provider": "disabled",
        "message": "设置 WEB_SEARCH_PROVIDER=brave|tavily 和 WEB_SEARCH_API_KEY 后启用联网搜索",
        "results": [],
    }


def test_brave_web_search_uses_supported_freshness_and_normalizes_output():
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"web":{"results":[{"title":"GraphRAG","url":"https://example.org/a","description":"evidence"}]}}'

    def opener(request, timeout):
        requests.append((request, timeout))
        return Response()

    settings = Settings(
        web_search_provider="brave",
        web_search_api_key="secret",
        web_search_max_results=5,
        web_search_timeout=3,
    )
    result = WebSearchService(settings, opener).search(
        "GraphRAG", max_results=3, recency_days=7
    )
    assert result["results"] == [
        {"title": "GraphRAG", "url": "https://example.org/a", "snippet": "evidence"}
    ]
    assert "freshness=pw" in requests[0][0].full_url
    assert requests[0][0].get_header("X-subscription-token") == "secret"
    assert requests[0][1] == 3
