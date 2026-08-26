import json
import re
from urllib.parse import urlparse

from formatters.utils import facts, unique


def _clean_agent_response(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        payload = json.loads(text)
        if isinstance(payload, dict) and payload.get("status") == "complete":
            return None
    except json.JSONDecodeError:
        pass
    text = re.sub(r"\[([^\]]+)]\(https?://[^)]+\)", r"\1", text)
    text = re.sub(r"https?://[^\s，。；;]+", "", text)
    text = re.sub(r"(?m)^\s*#{1,6}\s*", "", text)
    text = re.sub(r"[|]{2,}", " ", text)
    text = "\n".join(" ".join(line.split()) for line in text.splitlines() if line.strip())
    return text[:600].rstrip("。；;，, ") or None


def format_web(result: dict | None, resolved: dict[str, str]) -> tuple[str | None, bool]:
    """展示可点击外部来源；它们是候选证据，不参与图谱关系强结论。"""
    if not result:
        return None, False
    outputs = facts(result, "search_web")
    rows = unique(
        [row for output in outputs if isinstance(output, dict)
         for row in output.get("results", []) if isinstance(row, dict) and row.get("url")],
        "url",
    )
    if not rows:
        return "联网公开来源：当前搜索未返回可用网页结果", False
    agent_response = _clean_agent_response(result.get("response"))
    if agent_response:
        return (f"联网研究结论（待与来源交叉验证）：{agent_response}；"
                f"本次共返回 {len(rows)} 条公开来源，前 3 条见下方来源卡片"), False
    first = rows[0]
    domain = urlparse(str(first["url"])).hostname or "未知站点"
    snippet = " ".join(str(first.get("snippet") or "").replace("|", " ").split())[:140]
    summary = snippet or f"首条结果为《{str(first.get('title') or '未命名网页')[:60]}》"
    return (f"联网检索摘要（待交叉验证）：{summary}（来源：{domain}）；"
            f"本次共返回 {len(rows)} 条公开来源，前 3 条见下方来源卡片"), False
