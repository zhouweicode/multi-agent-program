"""Rule Validator：纯 Python 确定性校验，不调用 LLM。"""
import logging
from graph.state import GraphRAGState
from models.schemas import ValidationResult
from services.entity_service import EntityService
from services.evidence_service import EvidenceService
from services.observability import emit_event

logger = logging.getLogger(__name__)


def validator_node(state: GraphRAGState) -> dict:
    errors, missing = [], []
    entity_ids = set(state.get("resolved_entities", {}).values())
    entity_service, evidence_service = EntityService(), EvidenceService()
    for entity_id in entity_ids:
        if not entity_service.exists(entity_id):
            errors.append(f"entity_id 不存在: {entity_id}")
    agent_domains = {"talent_agent": "talent", "achievement_agent": "achievement", "enterprise_agent": "enterprise",
                     "industry_agent": "industry", "graph_reasoning_agent": "graph"}
    expected_domains = ({agent_domains[task["agent"]] for task in state.get("tasks", [])}
                        if state.get("complexity") == "complex" else {state.get("primary_domain", "achievement")})
    domain_results = (("talent", state.get("talent_result")), ("achievement", state.get("achievement_result")),
                      ("enterprise", state.get("enterprise_result")), ("industry", state.get("industry_result")),
                      ("graph", state.get("graph_result")))
    for domain, result in domain_results:
        if result is None and domain in expected_domains:
            missing.append(domain)
        elif result and result.get("errors"):
            errors.extend(result["errors"])
    for item in state.get("evidence", []):
        if not evidence_service.exists(item["evidence_id"]):
            errors.append(f"evidence_id 不存在: {item['evidence_id']}")
    achievement = state.get("achievement_result", {})
    common_papers, common_projects, aggregate = None, None, None
    for fact in achievement.get("facts", []):
        if fact["tool"] == "get_common_papers":
            common_papers = fact["data"]
            for paper in fact["data"]:
                if not entity_ids.issubset(set(paper["authors"])):
                    errors.append(f"共同论文作者校验失败: {paper['paper_id']}")
                if not isinstance(paper.get("year"), int) or not paper.get("evidence_id"):
                    errors.append(f"论文数据不完整: {paper.get('paper_id', 'UNKNOWN')}")
        if fact["tool"] == "get_common_projects":
            common_projects = fact["data"]
            for project in fact["data"]:
                if not entity_ids.issubset(set(project["participant_ids"])):
                    errors.append(f"共同项目参与者校验失败: {project['project_id']}")
                if project["start_year"] > project["end_year"]:
                    errors.append(f"项目时间范围无效: {project['project_id']}")
                if not project.get("evidence_id"):
                    errors.append(f"项目 evidence_id 缺失: {project['project_id']}")
        if fact["tool"] == "aggregate_cooperation":
            aggregate = fact["data"]
            if aggregate["common_paper_count"] != len(common_papers or []):
                errors.append("共同论文 count 与数据条数不一致")
    if achievement and (common_papers is None or common_projects is None or aggregate is None):
        errors.append("科研成果数据完整性校验失败：缺少论文、项目或聚合结果")
    graph_result = state.get("graph_result", {})
    for fact in graph_result.get("facts", []):
        if fact["tool"] == "find_path" and fact["data"].get("found"):
            path = fact["data"]
            if path["hop_count"] != len(path["edges"]) or len(path["nodes"]) != path["hop_count"] + 1:
                errors.append("图路径 hop_count 与节点/边数量不一致")
    industry_result = state.get("industry_result", {})
    for fact in industry_result.get("facts", []):
        if fact["tool"] == "rank_top_events":
            scores = [row["importance"] for row in fact["data"]]
            if scores != sorted(scores, reverse=True):
                errors.append("TOP 产业事件未按重要度降序排列")
    result = ValidationResult(valid=not errors and not missing, needs_replan=bool(missing or errors), missing_domains=missing, errors=errors)
    logger.info("Validator: valid=%s errors=%s", result.valid, result.errors)
    emit_event("RULE_VALIDATION_COMPLETED", thread_id=state.get("thread_id"), status="PASS" if result.valid else "FAIL",
               errors=result.errors, missing_domains=result.missing_domains)
    return {"validation_result": result.model_dump()}
