"""Deterministic lifecycle policy for bounded, reviewable user memory facts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from typing import Any, Literal

_SPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[，。！？；、,.!?;:\s]+")
_NEGATION_RE = re.compile(r"不要|禁止|不再|不能|无需|不需要|不|别")
_DURABLE_RE = re.compile(r"请记住|记住|以后|今后|后续|从现在起|始终|每次|长期|持续|固定|一直|默认")
_FORMAT_TERMS = ("表格", "json", "markdown", "分点", "要点", "纯文本", "段落")
_STYLE_OPPOSITES = (("简洁", "详细"), ("中文", "英文"), ("专业", "通俗"))


@dataclass(frozen=True)
class LifecycleDecision:
    action: Literal["create", "merge", "replace"]
    target: dict[str, Any] | None = None
    similarity: float = 0.0
    reason: str = "new_fact"


def normalize_memory_content(content: str) -> str:
    return _SPACE_RE.sub(" ", str(content or "")).strip().casefold()


def memory_similarity(left: str, right: str) -> float:
    left_normalized = normalize_memory_content(left)
    right_normalized = normalize_memory_content(right)
    if not left_normalized or not right_normalized:
        return 0.0
    sequence = SequenceMatcher(None, left_normalized, right_normalized).ratio()
    left_terms = set(_PUNCT_RE.sub("", left_normalized))
    right_terms = set(_PUNCT_RE.sub("", right_normalized))
    character = len(left_terms & right_terms) / max(1, len(left_terms | right_terms))
    return round(sequence * 0.7 + character * 0.3, 6)


def _subject_similarity(left: str, right: str) -> float:
    clean_left = _NEGATION_RE.sub("", _DURABLE_RE.sub("", left))
    clean_right = _NEGATION_RE.sub("", _DURABLE_RE.sub("", right))
    return memory_similarity(clean_left, clean_right)


def _is_conflict(existing: dict[str, Any], content: str, category: str) -> bool:
    if str(existing.get("category")) != category:
        return False
    old = normalize_memory_content(str(existing.get("content") or ""))
    new = normalize_memory_content(content)
    if bool(_NEGATION_RE.search(old)) != bool(_NEGATION_RE.search(new)):
        return _subject_similarity(old, new) >= 0.5
    if category == "output_format":
        old_formats = {term for term in _FORMAT_TERMS if term in old}
        new_formats = {term for term in _FORMAT_TERMS if term in new}
        if old_formats and new_formats and old_formats.isdisjoint(new_formats):
            return True
    return any(
        (left in old and right in new) or (right in old and left in new)
        for left, right in _STYLE_OPPOSITES
    )


def decide_lifecycle_action(
    existing: list[dict[str, Any]], content: str, category: str,
    similarity_threshold: float,
) -> LifecycleDecision:
    same_category = [fact for fact in existing if fact.get("category") == category]
    scored = sorted(
        ((memory_similarity(str(fact.get("content") or ""), content), fact)
         for fact in same_category),
        key=lambda item: (-item[0], str(item[1].get("fact_id") or "")),
    )
    if scored and scored[0][0] >= similarity_threshold:
        return LifecycleDecision("merge", scored[0][1], scored[0][0], "similar_fact")
    conflicts = [
        (memory_similarity(str(fact.get("content") or ""), content), fact)
        for fact in same_category if _is_conflict(fact, content, category)
    ]
    if conflicts:
        similarity, target = max(
            conflicts, key=lambda item: (item[0], str(item[1].get("updated_at") or ""))
        )
        return LifecycleDecision("replace", target, similarity, "contradictory_fact")
    return LifecycleDecision("create")


def review_due_at(days: int, now: datetime | None = None) -> str:
    current = now or datetime.now(UTC)
    return (current + timedelta(days=max(1, days))).isoformat()


def choose_eviction_candidate(facts: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not facts:
        return None
    return min(facts, key=lambda fact: (
        str(fact.get("category")) == "correction",
        float(fact.get("confidence") or 0),
        str(fact.get("last_recalled_at") or ""),
        str(fact.get("updated_at") or ""),
        str(fact.get("fact_id") or ""),
    ))
