"""发现可信 Skill 并执行其声明的离线评测门禁。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from skills.registry import skill_registry


def _configure_offline_gate_runtime(runtime_dir: Path) -> None:
    """门禁基线必须可重复，不能隐式调用部署环境的模型或数据库。"""
    os.environ["MODEL_PROVIDER"] = "mock"
    for agent in (
        "ROUTER_AGENT",
        "SUPERVISOR_AGENT",
        "TALENT_AGENT",
        "ACHIEVEMENT_AGENT",
        "ENTERPRISE_AGENT",
        "INDUSTRY_AGENT",
        "GRAPH_AGENT",
        "GRAPH_REASONING_AGENT",
        "WEB_AGENT",
        "WEB_RESEARCH_AGENT",
        "VERIFICATION_AGENT",
    ):
        os.environ[f"{agent}_MODEL_PROVIDER"] = "mock"
    os.environ.update(
        {
            "TOOL_TRANSPORT": "local",
            "TOOL_TRANSPORT_OVERRIDES_JSON": "{}",
            "MCP_SERVERS_JSON": "",
            "ENTITY_BACKEND": "mock",
            "ACHIEVEMENT_BACKEND": "mock",
            "ENTERPRISE_BACKEND": "mock",
            "INDUSTRY_BACKEND": "mock",
            "GRAPH_BACKEND": "mock",
            "WEB_SEARCH_PROVIDER": "disabled",
            "MEMORY_BACKEND": "sqlite",
            "MEMORY_RETRIEVAL_BACKEND": "mysql",
            "CONVERSATION_MEMORY_DB_PATH": str(runtime_dir / "conversation.sqlite"),
            "LONG_TERM_MEMORY_DB_PATH": str(runtime_dir / "long-term.sqlite"),
            "QUERY_EXPERIENCE_DB_PATH": str(runtime_dir / "experience.sqlite"),
        }
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="skill-gate-") as folder:
        _configure_offline_gate_runtime(Path(folder))
        rows = []
        for metadata in skill_registry.list():
            if not metadata["enabled"]:
                continue
            result = skill_registry.evaluate(str(metadata["skill_id"]))
            rows.append(
                {
                    "skill_id": result["skill_id"],
                    "content_hash": metadata["content_hash"],
                    "gate": result["gate"],
                }
            )
    print(json.dumps({"skills": rows}, ensure_ascii=False, indent=2))
    return 0 if all(item["gate"]["passed"] for item in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
