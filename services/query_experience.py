"""Shadow-mode query experience recall, scoring and terminal-run distillation."""
from __future__ import annotations

import hashlib
import json
import re
from difflib import SequenceMatcher
from threading import Lock
from typing import Any

from graph.state import GraphRAGState
from models.settings import Settings
from repositories.query_experience_repository import SQLiteQueryExperienceRepository
from services.observability import emit_event
from services.telemetry import traced_span

_repositories: dict[str, SQLiteQueryExperienceRepository] = {}
_repository_lock = Lock()
_SPACE_RE = re.compile(r"\s+")
_PUNCTUATION_RE = re.compile(r"[，。！？；：、,.!?;:\"'“”‘’（）()\[\]{}]")
_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_NUMBER_RE = re.compile(r"\d+")
_RESULT_FIELDS = ("talent_result", "achievement_result", "enterprise_result",
                  "industry_result", "graph_result", "web_result")


def query_experience_repository() -> SQLiteQueryExperienceRepository:
    path = Settings.from_env().query_experience_db_path
    with _repository_lock:
        repository = _repositories.get(path)
        if repository is None:
            repository = SQLiteQueryExperienceRepository(path)
            _repositories[path] = repository
        return repository


def close_query_experience() -> None:
    with _repository_lock:
        repositories = list(_repositories.values())
        _repositories.clear()
    for repository in repositories:
        repository.close()


def normalize_question(question: str) -> str:
    text = _SPACE_RE.sub("", question.strip().lower())
    return _PUNCTUATION_RE.sub("", text)


def query_template(question: str, mentions: list[str] | None = None) -> str:
    text = normalize_question(question)
    ordered = sorted(
        {item.strip() for item in mentions or [] if item.strip()},
        key=lambda item: (-len(item), item),
    )
    placeholders: list[tuple[str, str]] = []
    for index, mention in enumerate(ordered, 1):
        # Protect placeholder indexes from the generic number normalizer below.
        temporary = f"__scholar_{'x' * index}__"
        text = text.replace(normalize_question(mention), temporary)
        placeholders.append((temporary, f"{{SCHOLAR_{index}}}"))
    text = _YEAR_RE.sub("{YEAR}", text)
    text = _NUMBER_RE.sub("{NUMBER}", text)
    for temporary, placeholder in placeholders:
        text = text.replace(temporary, placeholder)
    return text


def pattern_id(scope_id: str, template: str) -> str:
    digest = hashlib.sha256(json.dumps({"scope": scope_id, "template": template},
                                       ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return f"exp-{digest[:20]}"


def _bigrams(text: str) -> set[str]:
    return {text[index:index + 2] for index in range(max(0, len(text) - 1))} or {text}


def template_similarity(left: str, right: str) -> float:
    if left == right:
        return 1.0
    sequence = SequenceMatcher(None, left, right).ratio()
    left_parts, right_parts = _bigrams(left), _bigrams(right)
    union = left_parts | right_parts
    jaccard = len(left_parts & right_parts) / len(union) if union else 0.0
    return round(sequence * 0.65 + jaccard * 0.35, 6)


def _pattern_score(pattern: dict[str, Any], similarity: float, settings: Settings) -> tuple[float, bool]:
    success_rate = float(pattern.get("success_rate") or 0)
    quality = min(max(float(pattern.get("average_quality") or 0), 0), 1)
    sample_confidence = min(int(pattern.get("sample_count") or 0) / max(settings.query_experience_min_samples, 1), 1)
    # Freshness is 1.0 in this first local implementation; the timestamp is
    # retained so production can later apply tenant-specific decay policies.
    experience_score = quality * 0.45 + success_rate * 0.25 + 0.15 + sample_confidence * 0.05
    confidence = round(similarity * experience_score, 6)
    applicable = (int(pattern.get("sample_count") or 0) >= settings.query_experience_min_samples
                  and confidence >= settings.query_experience_min_confidence)
    return confidence, applicable


def recall_query_experience(state: GraphRAGState) -> dict[str, Any]:
    if not state.get("experience_memory_enabled", True):
        emit_event("EXPERIENCE_RECALL_DISABLED", thread_id=state.get("thread_id"))
        return {"experience_recall_status": "DISABLED", "experience_candidates": [],
                "experience_match": None, "experience_strategy": {}}
    settings = Settings.from_env()
    question = state.get("question", "")
    template = query_template(question, state.get("entity_mentions", []))
    with traced_span("memory.experience.recall", "memory", {
        "run.id": state.get("thread_id"), "experience.mode": settings.query_experience_mode,
        "experience.query_template": template,
    }) as span:
        candidates = []
        for pattern in query_experience_repository().list_patterns(
                settings.query_experience_scope_id, limit=settings.query_experience_candidate_limit,
                positive_only=True):
            similarity = template_similarity(template, pattern["query_template"])
            if similarity < settings.query_experience_min_similarity:
                continue
            confidence, applicable = _pattern_score(pattern, similarity, settings)
            candidates.append(pattern | {"similarity": similarity, "confidence": confidence,
                                         "applicable": applicable})
        candidates.sort(key=lambda row: (-row["confidence"], -row["success_count"], row["pattern_id"]))
        candidates = candidates[:settings.query_experience_candidate_limit]
        match = candidates[0] if candidates else None
        if not match:
            span.set_attribute("experience.hit", False)
            emit_event("EXPERIENCE_RECALL_MISS", thread_id=state.get("thread_id"), query_template=template)
            return {"experience_recall_status": "MISS", "experience_query_template": template,
                    "experience_candidates": [], "experience_match": None, "experience_strategy": {},
                    "experience_mode": settings.query_experience_mode}

        strategy = match["strategy"]
        route_agreement = all(strategy.get(name) == state.get(name)
                              for name in ("primary_domain", "complexity"))
        public_match = {key: value for key, value in match.items() if key != "strategy"}
        public_match["route_agreement"] = route_agreement
        span.set_attribute("experience.hit", True)
        span.set_attribute("experience.pattern_id", match["pattern_id"])
        span.set_attribute("experience.confidence", match["confidence"])
        span.set_attribute("experience.applicable", match["applicable"])
        emit_event("EXPERIENCE_RECALL_HIT", thread_id=state.get("thread_id"),
                   pattern_id=match["pattern_id"], similarity=match["similarity"],
                   confidence=match["confidence"], sample_count=match["sample_count"],
                   applicable=match["applicable"], mode=settings.query_experience_mode)
        emit_event("EXPERIENCE_ROUTE_COMPARED", thread_id=state.get("thread_id"),
                   pattern_id=match["pattern_id"], agreement=route_agreement,
                   historical_domain=strategy.get("primary_domain"), current_domain=state.get("primary_domain"))
        return {"experience_recall_status": "HIT", "experience_query_template": template,
                "experience_candidates": [{key: value for key, value in row.items() if key != "strategy"}
                                          for row in candidates],
                "experience_match": public_match, "experience_strategy": strategy,
                "experience_route_agreement": route_agreement,
                "experience_mode": settings.query_experience_mode}


def _tool_strategy(state: GraphRAGState) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for field in _RESULT_FIELDS:
        domain_result = state.get(field) or {}
        if not domain_result:
            continue
        result[domain_result.get("agent", field)] = [call.get("name", "")
                                                      for call in domain_result.get("tool_calls", [])
                                                      if call.get("name")]
    return result


def _agents(state: GraphRAGState) -> list[str]:
    tasks = [task.get("agent") for task in state.get("tasks", []) if task.get("agent")]
    if tasks:
        return list(dict.fromkeys(tasks))
    mapping = {"talent": "talent_agent", "achievement": "achievement_agent",
               "enterprise": "enterprise_agent", "industry": "industry_agent",
               "graph": "graph_reasoning_agent", "web": "web_research_agent"}
    agent = mapping.get(state.get("primary_domain", ""))
    return [agent] if agent else []


def write_query_experience(state: GraphRAGState) -> dict[str, Any]:
    if not state.get("experience_memory_enabled", True):
        emit_event("EXPERIENCE_WRITEBACK_SKIPPED", thread_id=state.get("thread_id"), reason="disabled")
        return {"experience_writeback_status": "DISABLED"}
    validation = state.get("validation_result") or {}
    domain_errors = [error for field in _RESULT_FIELDS for error in (state.get(field) or {}).get("errors", [])]
    validation_pass = bool(validation.get("valid"))
    eligible = bool(state.get("final_answer")) and validation_pass and not domain_errors
    evidence_count = len(state.get("evidence", []))
    quality = max(0.0, min(1.0, (1.0 if validation_pass else 0.25)
                           - min(len(domain_errors) * 0.2, 0.6)
                           - (0.1 if validation_pass and not evidence_count else 0.0)))
    settings = Settings.from_env()
    template = state.get("experience_query_template") or query_template(
        state.get("question", ""), state.get("entity_mentions", []))
    strategy = {
        "intent": state.get("intent"), "complexity": state.get("complexity"),
        "primary_domain": state.get("primary_domain"),
        "requires_verification": bool(state.get("requires_verification")),
        "agents": _agents(state), "tools_by_agent": _tool_strategy(state),
        "web_search_enabled": bool(state.get("web_search_enabled")),
        "workflow_version": settings.workflow_version, "prompt_version": settings.prompt_version,
        "model_name": settings.model_name,
    }
    event = {
        "run_id": state.get("thread_id", ""), "scope_id": settings.query_experience_scope_id,
        "pattern_id": pattern_id(settings.query_experience_scope_id, template),
        "normalized_question": normalize_question(state.get("question", "")),
        "query_template": template, "strategy": strategy,
        "outcome": "SUCCESS" if eligible else "NEGATIVE", "eligible": eligible,
        "validation_pass": validation_pass, "quality_score": quality,
    }
    with traced_span("memory.experience.writeback", "memory", {
        "run.id": state.get("thread_id"), "experience.pattern_id": event["pattern_id"],
        "experience.eligible": eligible,
    }):
        written = query_experience_repository().record(event)
    event_name = "EXPERIENCE_WRITTEN" if written else "EXPERIENCE_WRITEBACK_SKIPPED"
    emit_event(event_name, thread_id=state.get("thread_id"), pattern_id=event["pattern_id"],
               eligible=eligible, outcome=event["outcome"], quality_score=quality,
               reason=None if written else "duplicate_run")
    pattern = query_experience_repository().get_pattern(event["pattern_id"])
    return {"experience_writeback_status": "WRITTEN" if written else "DUPLICATE",
            "experience_pattern": pattern}


def finalize_query_experience_metrics(run_id: str) -> None:
    """Attach metrics after the root Trace has been finalized."""
    try:
        from services.telemetry import repository as observability_repository
        trace = observability_repository().get_run(run_id)
        if trace:
            query_experience_repository().finalize_metrics(run_id, trace["summary"])
    except Exception:
        # Metrics enrichment must never change the user-visible query outcome.
        return


def query_experience_stats() -> dict[str, Any]:
    settings = Settings.from_env()
    return query_experience_repository().stats(settings.query_experience_scope_id) | {
        "scope_id": settings.query_experience_scope_id,
        "mode": settings.query_experience_mode,
        "min_samples": settings.query_experience_min_samples,
        "min_similarity": settings.query_experience_min_similarity,
        "min_confidence": settings.query_experience_min_confidence,
    }
