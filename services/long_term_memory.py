"""Passive, conservative extraction of user-authoritative long-term memory."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_SECRET_RE = re.compile(
    r"(?i)(password|passwd|api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"secret|authorization|密码|密钥|令牌)\s*[:=：]?\s*([^\s，。；,;]+)"
)
_BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_IDENTITY_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_SPLIT_RE = re.compile(r"[。！？!?；;\n]+")
_SPACE_RE = re.compile(r"\s+")

_DURABLE_MARKERS = (
    "请记住", "记住", "以后", "今后", "后续", "从现在起", "始终", "每次",
    "长期", "持续关注", "固定", "一直", "默认",
)
_PREFERENCE_MARKERS = ("偏好", "喜欢", "倾向于", "习惯", "更希望", "希望你")
_FOCUS_MARKERS = ("长期关注", "持续关注", "重点关注", "一直关注")
_CORRECTION_MARKERS = ("更正", "纠正", "请改为", "不是", "而是")
_CONSTRAINT_MARKERS = ("必须", "不要", "禁止", "只需", "只能", "应当", "需要遵守")
_FORMAT_MARKERS = (
    "输出格式", "报告格式", "回答格式", "固定格式", "表格", "markdown",
    "json", "要点", "分点", "简洁", "模板",
)


@dataclass(frozen=True)
class ExtractedFact:
    content: str
    category: str
    confidence: float


@dataclass(frozen=True)
class ExtractionResult:
    facts: list[ExtractedFact]
    rejected_count: int = 0
    sensitive: bool = False


def sanitize_memory_text(value: str, limit: int = 2000) -> tuple[str, bool]:
    """Redact secrets and direct contact identifiers before durable queueing."""
    text = str(value or "")
    sensitive = False
    substitutions = (
        (_SECRET_RE, lambda match: f"{match.group(1)}：[REDACTED]"),
        (_BEARER_RE, "Bearer [REDACTED]"),
        (_EMAIL_RE, "[REDACTED_EMAIL]"),
        (_PHONE_RE, "[REDACTED_PHONE]"),
        (_IDENTITY_RE, "[REDACTED_ID]"),
    )
    for pattern, replacement in substitutions:
        text, count = pattern.subn(replacement, text)
        sensitive = sensitive or count > 0
    text = text[:max(0, limit)]
    return text, sensitive


def build_memory_update_payload(state: dict[str, Any]) -> dict[str, Any]:
    question, question_sensitive = sanitize_memory_text(
        state.get("original_question") or state.get("question", ""), 2000
    )
    answer, answer_sensitive = sanitize_memory_text(state.get("final_answer", ""), 1200)
    return {
        "schema_version": 1,
        "user_message": question,
        "assistant_response": answer,
        # Worker fails closed when the source contained a sensitive value.
        "contains_sensitive_data": question_sensitive,
        "assistant_response_redacted": answer_sensitive,
        "intent": state.get("intent"),
        "primary_domain": state.get("primary_domain"),
        "resolved_entities": dict(state.get("resolved_entities") or {}),
    }


def _normalize_clause(clause: str) -> str:
    return _SPACE_RE.sub(" ", clause).strip(" ，,：:。")


def _classify(clause: str) -> tuple[str, float] | None:
    lower = clause.lower()
    durable = any(marker in clause for marker in _DURABLE_MARKERS)
    explicit_memory = "记住" in clause
    if not durable:
        return None
    if any(marker in clause for marker in _CORRECTION_MARKERS) and (
        explicit_memory or "更正" in clause or "纠正" in clause or "以后" in clause
    ):
        return "correction", 0.98
    if any(marker in lower for marker in _FORMAT_MARKERS) and (
        any(marker in clause for marker in _PREFERENCE_MARKERS + _CONSTRAINT_MARKERS)
        or explicit_memory or "以后" in clause or "每次" in clause or "固定" in clause
    ):
        return "output_format", 0.94
    if any(marker in clause for marker in _FOCUS_MARKERS):
        return "focus", 0.92
    if any(marker in clause for marker in _PREFERENCE_MARKERS):
        return "preference", 0.94
    if any(marker in clause for marker in _CONSTRAINT_MARKERS):
        return "constraint", 0.92
    if explicit_memory:
        return "context", 0.88
    return None


def extract_long_term_facts(payload: dict[str, Any]) -> ExtractionResult:
    """Extract only explicit and durable statements from the user's own words.

    Assistant output, tool output, and database facts are deliberately ignored.
    """
    if payload.get("contains_sensitive_data"):
        return ExtractionResult([], rejected_count=1, sensitive=True)
    message = str(payload.get("user_message") or "")
    if not message or len(message) > 4000:
        return ExtractionResult([], rejected_count=int(bool(message)))

    facts: list[ExtractedFact] = []
    rejected = 0
    seen: set[tuple[str, str]] = set()
    for raw_clause in _SPLIT_RE.split(message):
        clause = _normalize_clause(raw_clause)
        if not clause:
            continue
        if len(clause) > 500 or any(token in clause for token in (
            "[REDACTED]", "[REDACTED_EMAIL]", "[REDACTED_PHONE]", "[REDACTED_ID]"
        )):
            rejected += 1
            continue
        classified = _classify(clause)
        if classified is None:
            rejected += 1
            continue
        category, confidence = classified
        content = clause[:500]
        key = (category, " ".join(content.split()).casefold())
        if key not in seen:
            facts.append(ExtractedFact(content, category, confidence))
            seen.add(key)
    return ExtractionResult(facts, rejected_count=rejected)
