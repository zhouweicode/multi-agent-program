"""MCP Server、LangChain适配和local/mcp双模式契约测试。"""
from mcp import Client

from mcp_runtime.client import build_langchain_mcp_tools
from mcp_runtime.server import mcp
from models.settings import Settings
from services.web_search_service import WebSearchService
from tools.provider import get_tools, tool_names


def test_mcp_server_exposes_reusable_capabilities():
    async def inspect_server():
        async with Client(mcp) as client:
            result = await client.list_tools()
            return {item.name: item for item in result.tools}

    import asyncio
    tools = asyncio.run(inspect_server())
    assert len(tools) == 28
    assert {"get_person_profile", "get_author_papers", "find_path", "verify_evidence", "search_web"} <= set(tools)
    assert tools["find_path"].meta["domain"] == "graph"
    assert tools["search_web"].meta == {"domain": "web", "open_world": True}


def test_mcp_langchain_adapter_preserves_schema_and_result():
    remote = build_langchain_mcp_tools(mcp, ["get_person_profile", "find_path"],
                                       use_discovery_cache=False)
    assert [item.name for item in remote] == ["get_person_profile", "find_path"]
    assert "entity_id" in remote[0].args
    assert remote[0].metadata["tool_transport"] == "mcp"
    assert remote[0].invoke({"entity_id": "person_zw_001"})["name"] == "张伟"


def test_tool_provider_switches_transport_without_expanding_agent_allowlist():
    local = get_tools("talent", Settings(tool_transport="local"))
    remote = get_tools("talent", Settings(tool_transport="mcp"), mcp_target=mcp,
                       use_discovery_cache=False)
    assert [item.name for item in local] == tool_names("talent")
    assert [item.name for item in remote] == tool_names("talent")
    assert "search_web" not in [item.name for item in remote]
    args = {"entity_id": "person_zw_001"}
    assert remote[0].invoke(args) == local[0].invoke(args)


def test_web_search_is_a_safe_structured_error_when_not_configured():
    remote = build_langchain_mcp_tools(mcp, ["search_web"], use_discovery_cache=False)[0]
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

    settings = Settings(web_search_provider="brave", web_search_api_key="secret",
                        web_search_max_results=5, web_search_timeout=3)
    result = WebSearchService(settings, opener).search("GraphRAG", max_results=3, recency_days=7)
    assert result["results"] == [{"title": "GraphRAG", "url": "https://example.org/a", "snippet": "evidence"}]
    assert "freshness=pw" in requests[0][0].full_url
    assert requests[0][0].get_header("X-subscription-token") == "secret"
    assert requests[0][1] == 3
