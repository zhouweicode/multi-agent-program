"""可选联网搜索服务；密钥只从环境配置读取，结果被规范化后再交给 Agent。"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from models.settings import Settings

JsonOpener = Callable[..., Any]


class WebSearchService:
    """Brave/Tavily 的最小适配层，不允许模型控制请求地址或认证头。"""

    def __init__(self, settings: Settings | None = None, opener: JsonOpener = urlopen):
        self.settings = settings or Settings.from_env()
        self._opener = opener

    def health(self) -> dict:
        provider = self.settings.web_search_provider
        configured = provider in {"brave", "tavily"} and bool(self.settings.web_search_api_key)
        return {"ready": configured, "provider": provider,
                "message": None if configured else "请配置 WEB_SEARCH_PROVIDER 和 WEB_SEARCH_API_KEY"}

    def search(self, query: str, max_results: int = 5, recency_days: int | None = None,
               domains: list[str] | None = None) -> dict:
        query = query.strip()
        if not query:
            return {"error": "EMPTY_QUERY", "query": query, "results": []}
        provider = self.settings.web_search_provider
        if provider not in {"brave", "tavily"} or not self.settings.web_search_api_key:
            return {"error": "WEB_SEARCH_NOT_CONFIGURED", "query": query, "provider": provider,
                    "message": "设置 WEB_SEARCH_PROVIDER=brave|tavily 和 WEB_SEARCH_API_KEY 后启用联网搜索",
                    "results": []}
        limit = max(1, min(int(max_results), self.settings.web_search_max_results, 10))
        safe_domains = [item.strip().lower() for item in (domains or []) if item.strip()][:10]
        if provider == "brave":
            rows = self._search_brave(query, limit, recency_days)
        else:
            rows = self._search_tavily(query, limit, recency_days, safe_domains)
        if safe_domains and provider == "brave":
            rows = [row for row in rows if any(domain in row.get("url", "").lower() for domain in safe_domains)]
        return {"query": query, "provider": provider, "result_count": len(rows), "results": rows[:limit]}

    def _request_json(self, request: Request) -> dict:
        with self._opener(request, timeout=self.settings.web_search_timeout) as response:
            payload = response.read()
        return json.loads(payload.decode("utf-8"))

    def _search_brave(self, query: str, limit: int, recency_days: int | None) -> list[dict]:
        endpoint = self.settings.web_search_endpoint or "https://api.search.brave.com/res/v1/web/search"
        params: dict[str, Any] = {"q": query, "count": limit, "safesearch": "moderate"}
        if recency_days:
            params["freshness"] = self._freshness(max(1, int(recency_days)), brave=True)
        request = Request(f"{endpoint}?{urlencode(params)}", headers={
            "Accept": "application/json",
            "X-Subscription-Token": self.settings.web_search_api_key or "",
            "User-Agent": "tech-kg-mcp/1.0",
        })
        data = self._request_json(request)
        return [self._normalize(item.get("title"), item.get("url"), item.get("description"),
                                item.get("age") or item.get("page_age"))
                for item in data.get("web", {}).get("results", []) if item.get("url")]

    def _search_tavily(self, query: str, limit: int, recency_days: int | None,
                       domains: list[str]) -> list[dict]:
        endpoint = self.settings.web_search_endpoint or "https://api.tavily.com/search"
        payload: dict[str, Any] = {"api_key": self.settings.web_search_api_key, "query": query,
                                   "max_results": limit, "search_depth": "advanced",
                                   "include_answer": False, "include_raw_content": False}
        if domains:
            payload["include_domains"] = domains
        if recency_days:
            payload["time_range"] = self._freshness(max(1, int(recency_days)), brave=False)
        request = Request(endpoint, data=json.dumps(payload).encode("utf-8"), method="POST", headers={
            "Accept": "application/json", "Content-Type": "application/json",
            "User-Agent": "tech-kg-mcp/1.0",
        })
        data = self._request_json(request)
        return [self._normalize(item.get("title"), item.get("url"), item.get("content"),
                                item.get("published_date"), item.get("score"))
                for item in data.get("results", []) if item.get("url")]

    @staticmethod
    def _normalize(title: Any, url: Any, snippet: Any, published_at: Any = None,
                   score: Any = None) -> dict:
        row = {"title": str(title or "")[:500], "url": str(url or "")[:2000],
               "snippet": str(snippet or "")[:4000]}
        if published_at:
            row["published_at"] = str(published_at)[:100]
        if isinstance(score, (int, float)):
            row["score"] = float(score)
        return row

    @staticmethod
    def _freshness(days: int, brave: bool) -> str:
        if days <= 1:
            return "pd" if brave else "day"
        if days <= 7:
            return "pw" if brave else "week"
        if days <= 31:
            return "pm" if brave else "month"
        return "py" if brave else "year"
