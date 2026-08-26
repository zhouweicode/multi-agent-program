"""联网研究工具；local 和 MCP 两种传输使用相同业务实现。"""
from langchain_core.tools import tool

from services.web_search_service import WebSearchService


@tool
def search_web(query: str, max_results: int = 5, recency_days: int | None = None,
               domains: list[str] | None = None) -> dict:
    """联网搜索公开网页，返回标题、URL、摘要和可用的发布时间；不得把网页摘要直接当成已验证事实。"""
    return WebSearchService().search(query, max_results, recency_days, domains)
