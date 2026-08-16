"""Rule Validator：纯 Python 确定性校验，不调用 LLM。"""
import logging
from graph.state import GraphRAGState
from models.schemas import ValidationResult
from services.resources import get_entity_service, get_evidence_service
from services.observability import emit_event
from models.contracts import AGENT_DOMAINS, DEFAULT_REQUIRED_FACT_TYPES, FACT_TYPE_TO_TOOL

logger = logging.getLogger(__name__)


def validator_node(state: GraphRAGState) -> dict:
    errors, missing = [], []
    entity_ids = set(state.get("resolved_entities", {}).values())
    entity_service, evidence_service = get_entity_service(), get_evidence_service()
    for entity_id in entity_ids:
        if not entity_service.exists(entity_id):
            errors.append(f"entity_id 不存在: {entity_id}")
    expected_domains = ({AGENT_DOMAINS[task["agent"]] for task in state.get("tasks", [])}
                        if state.get("complexity") == "complex" else {state.get("primary_domain", "achievement")})
    domain_results = (("talent", state.get("talent_result")), ("achievement", state.get("achievement_result")),
                      ("enterprise", state.get("enterprise_result")), ("industry", state.get("industry_result")),
                      ("graph", state.get("graph_result")))
    for domain, result in domain_results:
        if result is None and domain in expected_domains:
            missing.append(domain)
        elif result and result.get("errors"):
            errors.extend(result["errors"])
    results_by_agent = {result["agent"]: result for _, result in domain_results if result}
    for task in state.get("tasks", []):
        result = results_by_agent.get(task["agent"])
        if not result:
            continue
        returned_tools = {fact.get("tool") for fact in result.get("facts", [])}
        required_fact_types = task.get("required_fact_types") or DEFAULT_REQUIRED_FACT_TYPES[task["agent"]]
        missing_facts = [fact_type for fact_type in required_fact_types
                         if FACT_TYPE_TO_TOOL.get(fact_type) not in returned_tools]
        expected_entities = set(task.get("required_entity_ids", []))
        if expected_entities and expected_entities != entity_ids:
            errors.append(f"任务实体契约不一致: {task['task_id']}")
        if missing_facts:
            domain = AGENT_DOMAINS[task["agent"]]
            errors.append(f"任务验收失败 {task.get('task_id', task['agent'])}：缺少事实类型 {', '.join(missing_facts)}")
            if domain not in missing:
                missing.append(domain)
    for item in state.get("evidence", []):
        # 新版证据携带原始事实快照，可在不重复访问数据库的情况下做确定性校验。
        complete = all(item.get(key) not in (None, "") for key in
                       ("evidence_id", "fact_type", "source_name", "source_record_id", "source_tool"))
        if not complete:
            errors.append(f"证据记录不完整: {item.get('evidence_id', 'UNKNOWN')}")
        elif not item.get("content") and not evidence_service.exists(item["evidence_id"], list(entity_ids)):
            errors.append(f"evidence_id 不存在: {item['evidence_id']}")
    achievement = state.get("achievement_result", {})
    author_papers, common_papers, common_projects, aggregate = None, None, None, None
    for fact in achievement.get("facts", []):
        if fact["tool"] == "get_author_papers":
            author_papers = fact["data"]
            for paper in fact["data"]:
                if not entity_ids.intersection(set(paper.get("authors", []))):
                    errors.append(f"作者论文归属校验失败: {paper.get('paper_id', 'UNKNOWN')}")
                if (not paper.get("paper_id") or not paper.get("title") or
                        not isinstance(paper.get("year"), int) or not paper.get("evidence_id")):
                    errors.append(f"论文数据不完整: {paper.get('paper_id', 'UNKNOWN')}")
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
        if fact["tool"] in {"get_person_patents", "get_common_patents"}:
            for patent in fact["data"]:
                if not entity_ids.issubset(set(patent.get("inventor_ids", []))):
                    errors.append(f"专利发明人归属校验失败: {patent.get('patent_id', 'UNKNOWN')}")
                if not patent.get("patent_id") or not patent.get("title") or not patent.get("evidence_id"):
                    errors.append(f"专利数据不完整: {patent.get('patent_id', 'UNKNOWN')}")
    collaboration_tools = {"get_common_papers", "get_common_projects", "aggregate_cooperation"}
    used_tools = {fact["tool"] for fact in achievement.get("facts", [])}
    if achievement and len(entity_ids) == 1 and "get_author_papers" not in used_tools and not (
            used_tools & (collaboration_tools | {"get_person_patents"})):
        errors.append("单人论文查询完整性校验失败：缺少作者论文结果")
    if achievement and bool(used_tools & collaboration_tools) and (
            common_papers is None or common_projects is None or aggregate is None):
        errors.append("科研合作数据完整性校验失败：缺少共同论文、共同项目或聚合结果")
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
