"""Rule Validator：纯 Python 确定性校验，不调用 LLM。"""
import logging

from graph.state import GraphRAGState
from models.contracts import (
    AGENT_DOMAINS,
    DEFAULT_REQUIRED_FACT_TYPES,
    FACT_TYPE_TO_TOOL,
)
from models.schemas import ValidationResult
from services.observability import emit_event
from services.resources import get_entity_service, get_evidence_service

logger = logging.getLogger(__name__)


def validator_node(state: GraphRAGState) -> dict:
    errors, missing, warnings = [], [], []
    entity_ids = set(state.get("resolved_entities", {}).values())
    skill_id = state.get("requested_skill")
    skill_required_domains = set(state.get("skill_required_domains", []))

    def add_domain_error(domain: str, message: str) -> None:
        if skill_id and domain not in skill_required_domains:
            warnings.append(f"可选领域 {domain}：{message}")
        else:
            errors.append(message)

    if skill_id == "expert_report" and len(entity_ids) != 1:
        errors.append("专家报告 Skill 只支持一个已完成消歧的专家实体")
    entity_service, evidence_service = get_entity_service(), get_evidence_service()
    for entity_id in entity_ids:
        if not entity_service.exists(entity_id):
            errors.append(f"entity_id 不存在: {entity_id}")
    expected_domains = ({AGENT_DOMAINS[task["agent"]] for task in state.get("tasks", [])}
                        if state.get("complexity") == "complex" else {state.get("primary_domain", "achievement")})
    domain_results = (("talent", state.get("talent_result")), ("achievement", state.get("achievement_result")),
                      ("enterprise", state.get("enterprise_result")), ("industry", state.get("industry_result")),
                      ("graph", state.get("graph_result")), ("web", state.get("web_result")))
    for domain, result in domain_results:
        if result is None and domain in expected_domains:
            if skill_id and domain not in skill_required_domains:
                warnings.append(f"可选领域 {domain} 未返回结果，报告将降级生成")
            else:
                missing.append(domain)
        elif result and result.get("errors"):
            for item in result["errors"]:
                add_domain_error(domain, item)
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
            message = f"任务验收失败 {task.get('task_id', task['agent'])}：缺少事实类型 {', '.join(missing_facts)}"
            if skill_id and domain not in skill_required_domains:
                warnings.append(f"可选领域 {domain}：{message}")
            else:
                errors.append(message)
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
    common_papers, common_projects, aggregate = None, None, None
    for fact in achievement.get("facts", []):
        if fact["tool"] == "get_author_papers":
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
                add_domain_error("graph", "图路径 hop_count 与节点/边数量不一致")
    industry_result = state.get("industry_result", {})
    if skill_id == "industry_landscape":
        industry_facts = {fact.get("tool"): fact.get("data") for fact in industry_result.get("facts", [])}
        if not industry_facts.get("search_industry_segments"):
            errors.append("产业全景报告未检索到候选产业节点")
        chain_data = industry_facts.get("get_chain_structure")
        if not isinstance(chain_data, dict) or chain_data.get("error"):
            errors.append("产业全景报告未取得有效产业链结构")
    for fact in industry_result.get("facts", []):
        if fact["tool"] == "rank_top_events":
            scores = [row["importance"] for row in fact["data"]]
            if scores != sorted(scores, reverse=True):
                errors.append("TOP 产业事件未按重要度降序排列")
    web_result = state.get("web_result", {})
    for fact in web_result.get("facts", []):
        if fact.get("tool") != "search_web":
            continue
        data = fact.get("data")
        if not isinstance(data, dict):
            add_domain_error("web", "联网搜索返回格式无效")
            continue
        if data.get("error"):
            add_domain_error("web", f"联网搜索失败: {data['error']}")
            continue
        rows = data.get("results")
        if data.get("provider") not in {"brave", "tavily"}:
            add_domain_error("web", "联网搜索来源提供方无效")
        if not isinstance(rows, list) or data.get("result_count") != len(rows):
            add_domain_error("web", "联网搜索结果计数与数据不一致")
            continue
        for row in rows:
            if not isinstance(row, dict) or not str(row.get("url", "")).startswith("https://"):
                add_domain_error("web", "联网证据 URL 无效或不是 HTTPS")
            if not row.get("title") or not row.get("snippet"):
                add_domain_error("web", "联网证据缺少标题或摘要")
    if web_result and not any(fact.get("tool") == "search_web" for fact in web_result.get("facts", [])):
        add_domain_error("web", "联网研究未返回 search_web 结果")
    if errors and "web" in expected_domains and "web" not in missing and web_result:
        web_errors = [item for item in errors if "联网" in item]
        if web_errors:
            missing.append("web")
    web_blocked_by_request = (not state.get("web_search_enabled", True) and bool(web_result)
                              and any("联网搜索已关闭" in item for item in web_result.get("errors", [])))
    if web_blocked_by_request and "web" in missing:
        missing.remove("web")
    only_policy_blocked = web_blocked_by_request and expected_domains == {"web"}
    result = ValidationResult(valid=not errors and not missing,
                              needs_replan=bool(missing or errors) and not only_policy_blocked,
                              missing_domains=missing, errors=errors, warnings=warnings)
    logger.info("Validator: valid=%s errors=%s", result.valid, result.errors)
    emit_event("RULE_VALIDATION_COMPLETED", thread_id=state.get("thread_id"), status="PASS" if result.valid else "FAIL",
               errors=result.errors, warnings=result.warnings, missing_domains=result.missing_domains)
    return {"validation_result": result.model_dump()}
