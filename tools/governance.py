"""Tool 输出安全边界与可审计回执。"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

_CONTROL_TAG = re.compile(
    r"<\s*/?\s*(?:system|developer|assistant|user|tool|instructions?|prompt)\b[^>]*>",
    re.IGNORECASE,
)
_INVISIBLE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]")
_REMOTE_INSTRUCTION = re.compile(
    r"(?:ignore|disregard|forget)\s+(?:all\s+)?(?:previous|prior)\s+instructions?"
    r"|(?:忽略|无视|忘记).{0,12}(?:先前|之前|以上|系统)?(?:指令|提示)",
    re.IGNORECASE,
)


def _stable_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def sanitize_remote_result(value: Any) -> tuple[Any, dict[str, int]]:
    """递归清除远程文本中的角色控制标签和不可见控制字符。"""
    stats = {
        "control_tags_removed": 0,
        "instruction_patterns_removed": 0,
        "invisible_chars_removed": 0,
    }

    def clean(item: Any) -> Any:
        if isinstance(item, str):

            def replace_tag(match: re.Match[str]) -> str:
                stats["control_tags_removed"] += 1
                return "[REMOTE_CONTROL_TAG_REMOVED]"

            result = _CONTROL_TAG.sub(replace_tag, item)
            result, count = _REMOTE_INSTRUCTION.subn(
                "[REMOTE_INSTRUCTION_REMOVED]", result
            )
            stats["instruction_patterns_removed"] += count
            result, count = _INVISIBLE.subn("", result)
            stats["invisible_chars_removed"] += count
            return result
        if isinstance(item, list):
            return [clean(child) for child in item]
        if isinstance(item, tuple):
            return [clean(child) for child in item]
        if isinstance(item, dict):
            return {clean(str(key)): clean(child) for key, child in item.items()}
        return item

    return clean(value), stats


def build_tool_receipt(
    *,
    visible_name: str,
    canonical_name: str,
    arguments: dict[str, Any],
    output: Any,
    raw_output: Any | None = None,
    success: bool,
    attempts: int,
    duration_ms: float,
    metadata: dict[str, Any] | None = None,
    sanitization: dict[str, int] | None = None,
) -> dict[str, Any]:
    """生成不保存参数和结果原文的确定性 Tool Receipt。"""
    metadata = metadata or {}
    sanitization = sanitization or {
        "control_tags_removed": 0,
        "instruction_patterns_removed": 0,
        "invisible_chars_removed": 0,
    }
    input_hash = _sha256(arguments)
    output_hash = _sha256(output)
    raw_output_hash = _sha256(output if raw_output is None else raw_output)
    transport = str(metadata.get("tool_transport") or "local")
    server_name = metadata.get("mcp_server_name")
    identity = {
        "tool": canonical_name,
        "visible_tool": visible_name,
        "transport": transport,
        "server_name": server_name,
        "input_sha256": input_hash,
        "output_sha256": output_hash,
        "status": "success" if success else "error",
    }
    encoded_output = _stable_json(output).encode("utf-8")
    return {
        "receipt_id": f"tr_{_sha256(identity)[:24]}",
        "tool": canonical_name,
        "visible_tool": visible_name,
        "transport": transport,
        "source": metadata.get("tool_source")
        or (f"mcp:{server_name}" if server_name else "local:repository"),
        "server_name": server_name,
        "trust_level": metadata.get("trust_level", "internal"),
        "status": identity["status"],
        "input_sha256": input_hash,
        "output_sha256": output_hash,
        "raw_output_sha256": raw_output_hash,
        "output_bytes": len(encoded_output),
        "attempts": attempts,
        "duration_ms": round(duration_ms, 2),
        "sanitized": any(sanitization.values()),
        "sanitization": sanitization,
    }
