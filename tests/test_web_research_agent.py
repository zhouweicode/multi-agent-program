from langgraph.types import Command

from agents.web_research_agent import build_web_research_agent
from formatters.web_formatter import format_web
from graph.builder import build_graph
from mcp_runtime.client import build_langchain_mcp_tools
from mcp_runtime.server import mcp
from nodes.validator_node import validator_node
from services.evidence_normalizer import normalize_tool_output
from tools.registry import tool_registry


class FakeWebSearchService:
    def search(self, query, max_results=5, recency_days=None, domains=None):
        rows = [
            {
                "title": "Neo4j GraphRAG documentation",
                "url": "https://neo4j.com/docs/neo4j-graphrag-python/current/",
                "snippet": "Official documentation for retrieval and GraphRAG components.",
                "published_at": "2026-01-01",
                "score": 0.98,
            },
            {
                "title": "Model Context Protocol Python SDK",
                "url": "https://py.sdk.modelcontextprotocol.io/",
                "snippet": "Official Python SDK documentation for MCP servers and clients.",
                "score": 0.91,
            },
        ][:max_results]
        return {"query": query, "provider": "tavily", "result_count": len(rows), "results": rows}


def _fake_web(monkeypatch):
    monkeypatch.setattr("tools.web_search_tools.WebSearchService", FakeWebSearchService)


def test_simple_web_query_routes_to_web_research_agent(monkeypatch):
    _fake_web(monkeypatch)
    final = build_graph().invoke({
        "question": "联网查证 Neo4j GraphRAG 最新检索能力，并给出官网来源。",
        "max_replans": 2,
        "replan_count": 0,
    }, config={"configurable": {"thread_id": "web-simple"}})

    assert final["primary_domain"] == "web"
    assert "plan" not in final
    assert final["web_result"]["agent"] == "web_research_agent"
    assert final["web_result"]["tool_calls"][0]["name"] == "search_web"
    assert final["validation_result"]["valid"] is True
    assert final["evidence"][0]["source_type"] == "web"
    assert "neo4j.com" in final["final_answer"]
    assert len(final["final_answer"]) < 900
    assert "前 3 条见下方来源卡片" in final["final_answer"]
    assert "未自动写入知识图谱" in final["final_answer"]



def test_web_research_agent_uses_prefixed_external_mcp_but_returns_canonical_fact(
    monkeypatch,
):
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    remote = build_langchain_mcp_tools(
        mcp,
        ["search_web"],
        use_discovery_cache=False,
        registry=tool_registry,
        server_name="public_web",
        name_prefix="external",
    )
    monkeypatch.setattr("agents.web_research_agent.get_tools", lambda _group: remote)

    result = build_web_research_agent().run("联网查证 GraphRAG", {})

    assert result["tool_calls"][0]["name"] == "search_web"
    assert result["facts"][0]["tool"] == "search_web"
    assert result["tool_receipts"][0]["visible_tool"] == "external__search_web"
    assert result["tool_receipts"][0]["source"] == "mcp:public_web"



def test_complex_query_fans_out_to_graph_and_web_agents(monkeypatch):
    _fake_web(monkeypatch)
    graph = build_graph()
    config = {"configurable": {"thread_id": "web-complex"}}
    first = graph.invoke({
        "question": "张伟与李明共同发表的论文，并联网查证最新公开资料。",
        "max_replans": 2,
        "replan_count": 0,
        "task_history": [],
    }, config=config)
    assert first["__interrupt__"]
    final = graph.invoke(Command(resume={"张伟": "person_zw_001", "李明": "person_lm_001"}), config=config)

    assert [task["agent"] for task in final["tasks"]] == ["achievement_agent", "web_research_agent"]
    assert final["achievement_result"]["agent"] == "achievement_agent"
    assert final["web_result"]["agent"] == "web_research_agent"
    assert final["validation_result"]["valid"] is True
    assert len(final["task_history"]) == 2


def test_web_evidence_id_is_stable_and_has_url_provenance():
    output = FakeWebSearchService().search("GraphRAG", max_results=1)
    first = normalize_tool_output("search_web", output, ["person_zw_001"])[0]
    second = normalize_tool_output("search_web", output, ["person_zw_001"])[0]

    assert first["evidence_id"] == second["evidence_id"]
    assert first["evidence_id"].startswith("web_")
    assert first["source_name"] == "web:neo4j.com"
    assert first["source_record_id"].startswith("https://neo4j.com/")
    assert first["content"]["provider"] == "tavily"


def test_web_formatter_prefers_agent_answer_and_removes_raw_url():
    text, supported = format_web({
        "response": "清华大学创建于1911年。依据可参见[学校沿革](https://www.tsinghua.edu.cn/about/history)。",
        "facts": [{"tool": "search_web", "data": FakeWebSearchService().search("清华大学", max_results=1)}],
    }, {})

    assert "清华大学创建于1911年" in text
    assert "学校沿革" in text
    assert "https://" not in text
    assert supported is False


def test_web_provider_error_fails_validation_and_requests_replan():
    validation = validator_node({
        "question": "联网搜索 GraphRAG",
        "complexity": "simple",
        "primary_domain": "web",
        "resolved_entities": {},
        "web_result": {
            "agent": "web_research_agent",
            "errors": [],
            "facts": [{"tool": "search_web", "data": {
                "error": "WEB_SEARCH_NOT_CONFIGURED", "provider": "disabled", "results": [],
            }}],
        },
        "evidence": [],
    })["validation_result"]

    assert validation["valid"] is False
    assert validation["needs_replan"] is True
    assert validation["missing_domains"] == ["web"]
    assert "WEB_SEARCH_NOT_CONFIGURED" in validation["errors"][0]


def test_disabled_web_search_removes_web_from_mixed_query():
    final = build_graph().invoke({
        "question": "查询人工智能产业链最新新闻并联网查证。",
        "web_search_enabled": False,
        "max_replans": 2,
        "replan_count": 0,
    }, config={"configurable": {"thread_id": "web-disabled-mixed"}})

    assert final["primary_domain"] == "industry"
    assert final["complexity"] == "simple"
    assert final["industry_result"]["agent"] == "industry_agent"
    assert final.get("web_result") is None
    assert final["validation_result"]["valid"] is True


def test_disabled_web_only_query_never_builds_external_agent(monkeypatch):
    def fail_if_built():
        raise AssertionError("联网关闭时不应构建 WebResearchAgent")

    monkeypatch.setattr("nodes.agent_nodes.build_web_research_agent", fail_if_built)
    final = build_graph().invoke({
        "question": "联网查证 Neo4j GraphRAG 最新官网资料。",
        "web_search_enabled": False,
        "max_replans": 2,
        "replan_count": 0,
    }, config={"configurable": {"thread_id": "web-disabled-only"}})

    assert final["web_result"]["tool_calls"] == []
    assert final["web_result"]["facts"] == []
    assert "联网搜索已关闭" in final["web_result"]["errors"][0]
    assert final["validation_result"]["needs_replan"] is False
    assert "请在前端开启后重试" in final["final_answer"]
