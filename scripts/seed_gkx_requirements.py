"""Generate and transactionally seed requirement-aligned demo data into ``gkx``.

This command is intentionally separate from ``import_synthetic_gkx.py``.  It targets the
existing production-shaped DWD tables, defaults to a no-write preview, never deletes rows,
and requires an exact database confirmation before applying a single transaction.
"""
from __future__ import annotations

import argparse
import json
import math
import uuid
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from models.settings import Settings

SOURCE = "gkx_requirement_seed_v1_20260827"
SCHOLAR_COUNT = 1_000
ORG_COUNT = 1_000
PAPER_COUNT = 1_000
PROJECT_COUNT = 1_000
PATENT_COUNT = 1_000
INDUSTRY_NODE_COUNT = 1_000
NEWS_COUNT = 1_000
PAPER_ID_BASE = 920_260_827_000_000

SURNAMES = tuple("王李张刘陈杨黄赵吴周徐孙马朱胡郭何林高罗")
GIVEN_NAMES = ("伟", "芳", "娜", "敏", "静", "强", "磊", "洋", "勇", "艳")
NAME_QUALIFIERS = ("博", "文", "宇", "宁", "远")
FIELDS = ("人工智能", "知识图谱", "生物医药", "新能源", "新材料", "机器人", "量子信息", "集成电路", "智能制造", "遥感科学")
CITIES = ("北京", "上海", "深圳", "广州", "杭州", "南京", "武汉", "西安", "成都", "合肥")
PROVINCES = ("北京市", "上海市", "广东省", "广东省", "浙江省", "江苏省", "湖北省", "陕西省", "四川省", "安徽省")
POSITIONS = ("教授", "副教授", "研究员", "副研究员", "高级工程师")
POSITION_EN = {"教授": "Professor", "副教授": "Associate Professor", "研究员": "Researcher",
               "副研究员": "Associate Researcher", "高级工程师": "Senior Engineer"}

TABLE_COLUMNS = {
    "dwd_org_base_info": (
        "org_id", "name_cn", "external_id", "province", "city", "area", "address",
        "addr_lng", "addr_lat", "postal_code", "email", "lerep", "reg_status",
        "registration_org", "incorporation_year", "incorporation_date", "start_date",
        "end_date", "org_type", "listing_status", "listing_date",
        "registered_capital_value", "capital_currency", "industry", "industry_l1_name",
        "industry_l1_code", "industry_l2_name", "industry_l2_code", "industry_l3_name",
        "industry_l3_code", "industry_l4_name", "industry_l4_code", "data_source",
        "created_time", "updated_time",
    ),
    "dwd_scholar": (
        "scholar_id", "name_en", "name_zh", "avatar", "scholar_org_name_en",
        "scholar_org_name_zh", "bio", "bio_zh", "work_experience_date",
        "work_experience_institution_en", "work_experience_department_en",
        "work_experience_position_en", "work_experience_institution_zh",
        "work_experience_department_zh", "work_experience_position_zh",
        "education_background_date", "education_background_institution_en",
        "education_background_degree_en", "education_background_institution_zh",
        "education_background_degree_zh", "paper_nums", "citation_nums", "h_index",
        "status", "create_time", "update_time",
    ),
    "dwd_scholar_research_direction": ("scholar_id", "fields", "create_time", "update_time"),
    "dwd_scholar_talent_flag": ("scholar_id", "academician", "create_time", "update_time"),
    "dwd_scholar_papers": (
        "id", "zh_name", "en_name", "authors", "paper_url", "cover_date_start",
        "create_time", "update_time", "status", "zh_abstract", "en_abstract", "doi",
        "publication_en_name",
    ),
    "dwd_scholar_paper_relation": (
        "paper_id", "year", "scholar_id", "citations", "publish_time", "status",
        "create_time", "update_time", "publication_id", "related_paper_id",
    ),
    "dwd_scholar_coauthor": (
        "scholar_id", "co_scholar_id", "co_scholar_name_en", "co_scholar_name_zh",
        "co_scholar_avatar", "co_scholar_org_name_en", "co_scholar_org_name_zh",
        "co_paper_count", "status", "create_time", "update_time",
    ),
    "dwd_zh_project": (
        "id", "project_number", "title", "project_source", "funded_institution",
        "project_level", "funded_amount", "discipline", "discipline_code", "fund_category",
        "funded_province", "participating_institution", "approval_year", "approval_time",
        "research_period", "project_host", "participants", "keywords", "abstract",
        "final_report_abstract", "project_page_url", "updated_time", "create_time",
    ),
    "dwd_zh_project_output": (
        "id", "total_outputs", "journal_articles_count", "conference_papers_count",
        "degree_papers_count", "patents_count", "books_count", "awards_count",
        "reports_count", "other_outputs_count", "output_journal_articles", "output_patents",
        "output_conference_papers", "output_degree_papers", "output_books", "output_awards",
        "output_reports", "output_other", "updated_time", "create_time",
    ),
    "dwd_patent": (
        "id", "patent_id", "publication_number", "application_kind", "country_code", "country",
        "publication_reference", "application_reference", "pct_or_regional_filing_data",
        "pct_or_regional_publishing_data", "priority_filings", "applicants", "assignees",
        "inventors", "first_applicant_name", "first_current_assignee_name",
        "first_inventor_name", "main_classification_ipcr", "further_classification_ipcr",
        "main_classification_cpc", "further_classification_cpc", "keywords", "claims",
        "description", "figures", "language", "granted_number", "db_source", "create_time",
        "update_time", "value", "agents", "agency", "examiners", "related_documents",
        "classification_loc", "classification_fi", "classification_upc", "classification_fterm",
    ),
    "dwd_patent_title": (
        "id", "patent_id", "titles", "title_localized", "title_zh", "db_source",
        "create_time", "update_time",
    ),
    "dwd_industry_chain_info": (
        "chain_code", "chain_name", "node_id", "node_name", "node_type", "level", "node_seq",
        "parent_id", "parent_name", "node_imp_level", "downstream_link_code", "node_stage",
        "node_path", "data_source", "created_time", "updated_time",
    ),
    "dwd_industry_chain_news_info": (
        "chain_code", "chain_name", "news_id", "title", "relaese_date", "summary", "source",
        "data_source", "created_time", "updated_time",
    ),
    "dwd_org_industry_chain_dtl": (
        "chain_code", "chain_name", "node_id", "node_name", "antitypic", "credit_code",
        "chain_score", "data_source", "created_time", "updated_time",
    ),
    "dwd_org_industry_chain_pat_dtl": (
        "chain_code", "chain_name", "node_id", "node_name", "apno", "apdt", "pat_name", "pn",
        "pbdt", "current_assign", "inventors", "data_source", "created_time", "updated_time",
    ),
    "dwd_org_industry_chain_prod_dtl": (
        "chain_code", "chain_name", "antitypic", "company_name", "credit_code", "tech_product",
        "tech_product_seq", "data_source", "created_time", "updated_time",
    ),
    "dwd_org_important_news_info": (
        "org_id", "name_cn", "external_id", "news_title", "news_date", "news_content",
        "original_textlink", "data_source", "created_time", "updated_time",
    ),
}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _dt(year: int, month: int, day: int, hour: int = 0) -> datetime:
    """Return a naive value because the target MySQL columns are timezone-less DATETIME."""
    return datetime(year, month, day, hour)  # noqa: DTZ001


def _scholar_id(index: int) -> str:
    return f"GKXREQSCH{index:06d}"


def _scholar_name(index: int) -> str:
    cycle = len(SURNAMES) * len(GIVEN_NAMES)
    return (SURNAMES[(index - 1) % len(SURNAMES)]
            + GIVEN_NAMES[((index - 1) // len(SURNAMES)) % len(GIVEN_NAMES)]
            + NAME_QUALIFIERS[((index - 1) // cycle) % len(NAME_QUALIFIERS)])


def _scholar_name_en(index: int) -> str:
    return f"Requirement Scholar {index:04d}"


def _org_id(index: int) -> str:
    return f"GKXREQORG{index:06d}"


def _credit_code(index: int) -> str:
    return f"91310100MA{index:08d}"


def _org_name(index: int) -> str:
    city = CITIES[(index - 1) % len(CITIES)]
    field = FIELDS[(index - 1) % len(FIELDS)]
    if index <= 50:
        return f"{city}{field}大学{index:03d}"
    if index <= 100:
        return f"{city}{field}研究院{index:03d}"
    return f"{city}{field}科技有限公司{index:04d}"


def _org_name_en(index: int) -> str:
    if index <= 50:
        kind = "University"
    elif index <= 100:
        kind = "Research Institute"
    else:
        kind = "Technology Co., Ltd."
    return f"GKX Requirement {kind} {index:04d}"


def _patent_id(index: int) -> str:
    return f"GKXREQPAT{index:08d}"


def _publication_number(index: int) -> str:
    return f"CN202610{index:06d}A"


def _industry_node(index: int) -> dict[str, Any]:
    chain_number = (index - 1) // 100 + 1
    local = (index - 1) % 100 + 1
    chain_code = f"GKXREQCHAIN{chain_number:03d}"
    chain_name = f"{FIELDS[chain_number - 1]}产业链"
    node_id = f"{chain_code}N{local:03d}"
    if local == 1:
        level, parent_local = 1, None
    elif local <= 10:
        level, parent_local = 2, 1
    else:
        level, parent_local = 3, ((local - 11) // 10) + 2
    parent_id = f"{chain_code}N{parent_local:03d}" if parent_local else None
    parent_name = (f"{chain_name}{'核心层' if parent_local == 1 else f'环节{parent_local:02d}'}"
                   if parent_local else None)
    node_name = f"{chain_name}{'核心层' if local == 1 else f'环节{local:02d}'}"
    if local == 1:
        downstream = f"{chain_code}N002"
    elif local <= 10:
        downstream = f"{chain_code}N{11 + (local - 2) * 10:03d}"
    else:
        downstream = None
    path_names = [chain_name]
    if parent_name:
        path_names.append(parent_name)
    if node_name != parent_name:
        path_names.append(node_name)
    return {"chain_code": chain_code, "chain_name": chain_name, "node_id": node_id,
            "node_name": node_name, "node_type": 1 if local == 1 else 2, "level": level,
            "node_seq": local, "parent_id": parent_id, "parent_name": parent_name,
            "node_imp_level": 1 + (index % 5), "downstream_link_code": downstream,
            "node_stage": level, "node_path": ">".join(path_names)}


def generate_rows() -> dict[str, list[dict[str, Any]]]:
    now = _dt(2026, 8, 27, 10)
    tables = {table: [] for table in TABLE_COLUMNS}
    organizations: list[dict[str, Any]] = []
    for index in range(1, ORG_COUNT + 1):
        city_index = (index - 1) % len(CITIES)
        field = FIELDS[(index - 1) % len(FIELDS)]
        kind = "高等院校" if index <= 50 else ("科研院所" if index <= 100 else "科技企业")
        row = {
            "org_id": _org_id(index), "name_cn": _org_name(index), "external_id": _credit_code(index),
            "province": PROVINCES[city_index], "city": CITIES[city_index], "area": "高新技术产业开发区",
            "address": f"{CITIES[city_index]}市科技大道{index}号", "addr_lng": f"{116.10 + index % 100 / 100:.6f}",
            "addr_lat": f"{31.10 + index % 80 / 100:.6f}", "postal_code": f"{100000 + index % 899999:06d}",
            "email": f"contact{index:04d}@example.invalid", "lerep": _scholar_name(index),
            "reg_status": "存续", "registration_org": f"{CITIES[city_index]}市市场监督管理局",
            "incorporation_year": 1990 + index % 35,
            "incorporation_date": _dt(1990 + index % 35, index % 12 + 1, index % 27 + 1),
            "start_date": f"{1990 + index % 35}-01-01", "end_date": "长期", "org_type": kind,
            "listing_status": "未上市" if index % 8 else "上市", "listing_date": None if index % 8 else _dt(2018, 1, 1),
            "registered_capital_value": Decimal(10_000_000 + index * 50_000), "capital_currency": "人民币",
            "industry": field, "industry_l1_name": "科学研究和技术服务业", "industry_l1_code": "M",
            "industry_l2_name": field, "industry_l2_code": f"M{70 + index % 20:02d}",
            "industry_l3_name": f"{field}研发", "industry_l3_code": f"M{730 + index % 60:03d}",
            "industry_l4_name": f"{field}技术服务", "industry_l4_code": f"M{7300 + index % 600:04d}",
            "data_source": SOURCE, "created_time": now, "updated_time": now,
        }
        organizations.append(row)
        tables["dwd_org_base_info"].append(row)

    scholar_meta = {
        index: {"scholar_id": _scholar_id(index), "name_zh": _scholar_name(index),
                "name_en": _scholar_name_en(index), "org_index": (index - 1) % 200 + 1}
        for index in range(1, SCHOLAR_COUNT + 1)
    }
    paper_counts: Counter[int] = Counter()
    citation_counts: Counter[int] = Counter()
    coauthors: Counter[tuple[int, int]] = Counter()
    for index in range(1, PAPER_COUNT + 1):
        paper_id = PAPER_ID_BASE + index
        field = FIELDS[(index - 1) % len(FIELDS)]
        author_indexes = (index, index % SCHOLAR_COUNT + 1, (index + 9) % SCHOLAR_COUNT + 1)
        authors = [{"scholar_id": _scholar_id(item), "name": _scholar_name(item)} for item in author_indexes]
        year = 2017 + index % 10
        published = _dt(year, index % 12 + 1, index % 27 + 1)
        tables["dwd_scholar_papers"].append({
            "id": paper_id, "zh_name": f"{field}关键技术与协同创新研究{index:04d}",
            "en_name": f"Collaborative Research on {field} Technology {index:04d}",
            "authors": _json(authors), "paper_url": f"https://example.invalid/gkx-requirement/papers/{index:04d}",
            "cover_date_start": published, "create_time": now, "update_time": now, "status": 1,
            "zh_abstract": f"围绕{field}开展方法、应用与产业协同研究。",
            "en_abstract": f"A requirement-aligned study of methods and applications in {field}.",
            "doi": f"10.20268/gkxreq.{index:06d}", "publication_en_name": f"GKX Science Journal {index % 20 + 1}",
        })
        for order, scholar_index in enumerate(author_indexes, 1):
            citations = 5 + (index * 7 + order * 3) % 196
            paper_counts[scholar_index] += 1
            citation_counts[scholar_index] += citations
            tables["dwd_scholar_paper_relation"].append({
                "paper_id": paper_id, "year": year, "scholar_id": _scholar_id(scholar_index),
                "citations": citations, "publish_time": published, "status": 1,
                "create_time": now, "update_time": now, "publication_id": 800_000 + index % 20,
                "related_paper_id": paper_id,
            })
        for left in author_indexes:
            for right in author_indexes:
                if left != right:
                    coauthors[(left, right)] += 1

    for index in range(1, SCHOLAR_COUNT + 1):
        meta = scholar_meta[index]
        org = organizations[meta["org_index"] - 1]
        education_org_index = (index - 1) % 40 + 1
        education_org = organizations[education_org_index - 1]
        field = FIELDS[(index - 1) % len(FIELDS)]
        position = POSITIONS[(index - 1) % len(POSITIONS)]
        start_year = 2008 + index % 14
        tables["dwd_scholar"].append({
            "scholar_id": meta["scholar_id"], "name_en": meta["name_en"], "name_zh": meta["name_zh"],
            "avatar": f"https://example.invalid/gkx-requirement/avatar/{index:04d}.png",
            "scholar_org_name_en": _org_name_en(meta["org_index"]), "scholar_org_name_zh": org["name_cn"],
            "bio": f"Researcher in {field}, collaborative innovation and technology transfer.",
            "bio_zh": f"长期从事{field}、协同创新与科技成果转化研究。",
            "work_experience_date": f"{start_year}.09-至今",
            "work_experience_institution_en": _org_name_en(meta["org_index"]),
            "work_experience_department_en": f"School of {field}",
            "work_experience_position_en": POSITION_EN[position],
            "work_experience_institution_zh": org["name_cn"],
            "work_experience_department_zh": f"{field}研究中心", "work_experience_position_zh": position,
            "education_background_date": f"{start_year - 8}.09-{start_year - 3}.06",
            "education_background_institution_en": _org_name_en(education_org_index),
            "education_background_degree_en": "Ph.D.",
            "education_background_institution_zh": education_org["name_cn"],
            "education_background_degree_zh": f"{field}博士", "paper_nums": paper_counts[index],
            "citation_nums": citation_counts[index], "h_index": min(80, max(1, int(math.sqrt(citation_counts[index])))),
            "status": 1, "create_time": now, "update_time": now,
        })
        tables["dwd_scholar_research_direction"].append({
            "scholar_id": meta["scholar_id"], "fields": f"{field}；知识图谱；科技成果转化",
            "create_time": now, "update_time": now,
        })
        tables["dwd_scholar_talent_flag"].append({
            "scholar_id": meta["scholar_id"], "academician": "1" if index % 50 == 0 else "0",
            "create_time": now, "update_time": now,
        })

    for (left, right), count in sorted(coauthors.items()):
        co_meta = scholar_meta[right]
        co_org_index = co_meta["org_index"]
        tables["dwd_scholar_coauthor"].append({
            "scholar_id": _scholar_id(left), "co_scholar_id": co_meta["scholar_id"],
            "co_scholar_name_en": co_meta["name_en"], "co_scholar_name_zh": co_meta["name_zh"],
            "co_scholar_avatar": f"https://example.invalid/gkx-requirement/avatar/{right:04d}.png",
            "co_scholar_org_name_en": _org_name_en(co_org_index),
            "co_scholar_org_name_zh": organizations[co_org_index - 1]["name_cn"],
            "co_paper_count": count, "status": 1, "create_time": now, "update_time": now,
        })

    for index in range(1, PROJECT_COUNT + 1):
        field = FIELDS[(index - 1) % len(FIELDS)]
        participants_indexes = (index, index % SCHOLAR_COUNT + 1,
                                (index + 9) % SCHOLAR_COUNT + 1, (index + 99) % SCHOLAR_COUNT + 1)
        participant_names = [_scholar_name(item) for item in participants_indexes]
        approval_year = 2018 + index % 9
        project_id = f"GKXREQ-PROJ-{index:06d}"
        tables["dwd_zh_project"].append({
            "id": project_id, "project_number": f"GKX-{approval_year}-{index:06d}",
            "title": f"{field}协同创新与产业应用项目{index:04d}", "project_source": "国家重点研发计划",
            "funded_institution": "科学技术部", "project_level": "国家级",
            "funded_amount": Decimal(500_000 + index * 1_000), "discipline": field,
            "discipline_code": f"E{index % 10:02d}.{index % 100:02d}", "fund_category": "重点项目",
            "funded_province": PROVINCES[(index - 1) % len(PROVINCES)],
            "participating_institution": _json([organizations[(index - 1) % 200]["name_cn"],
                                                  organizations[(index + 16) % 200]["name_cn"]]),
            "approval_year": approval_year, "approval_time": _dt(approval_year, 1, 1),
            "research_period": f"{approval_year}.01-{approval_year + 3}.12",
            "project_host": participant_names[0], "participants": _json(participant_names),
            "keywords": _json([field, "协同创新", "产业应用"]),
            "abstract": f"面向{field}关键问题开展多单位联合攻关。",
            "final_report_abstract": f"形成{field}论文、专利和示范应用成果。",
            "project_page_url": f"https://example.invalid/gkx-requirement/projects/{index:04d}",
            "updated_time": now, "create_time": now,
        })
        output_counts = (2 + index % 4, 1 + index % 2, index % 2, 1, index % 2, index % 3 == 0, 1, index % 2)
        total = sum(int(value) for value in output_counts)
        tables["dwd_zh_project_output"].append({
            "id": project_id, "total_outputs": total, "journal_articles_count": output_counts[0],
            "conference_papers_count": output_counts[1], "degree_papers_count": output_counts[2],
            "patents_count": output_counts[3], "books_count": output_counts[4],
            "awards_count": int(output_counts[5]), "reports_count": output_counts[6],
            "other_outputs_count": output_counts[7],
            "output_journal_articles": _json([{"paper_id": PAPER_ID_BASE + index,
                                                "title": f"{field}关键技术与协同创新研究{index:04d}"}]),
            "output_patents": _json([{"patent_id": _patent_id(index),
                                       "title": f"一种{field}协同处理装置及方法{index:04d}"}]),
            "output_conference_papers": _json([]), "output_degree_papers": _json([]),
            "output_books": _json([]), "output_awards": _json([]),
            "output_reports": _json([f"{field}项目年度报告{index:04d}"]), "output_other": _json([]),
            "updated_time": now, "create_time": now,
        })

    for index in range(1, PATENT_COUNT + 1):
        field = FIELDS[(index - 1) % len(FIELDS)]
        inventor_indexes = (index, (index + 4) % SCHOLAR_COUNT + 1, (index + 49) % SCHOLAR_COUNT + 1)
        inventors = [{"scholar_id": _scholar_id(item), "name": _scholar_name(item)} for item in inventor_indexes]
        assignee_org = organizations[index - 1]
        application_date = _dt(2019 + index % 7, index % 12 + 1, index % 27 + 1)
        title_zh = f"一种{field}协同处理装置及方法{index:04d}"
        title_en = f"Collaborative {field} processing apparatus and method {index:04d}"
        patent_id = _patent_id(index)
        publication_number = _publication_number(index)
        tables["dwd_patent"].append({
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{SOURCE}:patent:{index}")),
            "patent_id": patent_id, "publication_number": publication_number, "application_kind": "A",
            "country_code": "CN", "country": "中国",
            "publication_reference": _json({"document_number": publication_number, "date": application_date.date().isoformat()}),
            "application_reference": _json({"application_number": f"CN2026{index:08d}", "date": application_date.date().isoformat()}),
            "pct_or_regional_filing_data": None, "pct_or_regional_publishing_data": None,
            "priority_filings": _json([{"country": "CN", "date": application_date.date().isoformat()}]),
            "applicants": _json([{"org_id": assignee_org["org_id"], "name": assignee_org["name_cn"]}]),
            "assignees": _json([{"org_id": assignee_org["org_id"], "name": assignee_org["name_cn"]}]),
            "inventors": _json(inventors), "first_applicant_name": assignee_org["name_cn"],
            "first_current_assignee_name": assignee_org["name_cn"], "first_inventor_name": inventors[0]["name"],
            "main_classification_ipcr": f"G06F{index % 30:02d}/00", "further_classification_ipcr": _json([]),
            "main_classification_cpc": f"G06F{index % 30:02d}/10", "further_classification_cpc": _json([]),
            "keywords": _json([field, "协同处理", "智能装置"]),
            "claims": _json({"zh": [f"1.一种用于{field}的协同处理装置。"]}),
            "description": _json({"zh": f"本发明属于{field}技术领域。"}), "figures": _json([]),
            "language": _json(["zh"]), "granted_number": publication_number[:-1] + "B",
            "db_source": SOURCE, "create_time": now, "update_time": now, "value": 100_000 + index * 100,
            "agents": _json(["示例专利代理人"]), "agency": _json(["示例知识产权代理有限公司"]),
            "examiners": _json(["示例审查员"]), "related_documents": _json([]),
            "classification_loc": _json([]), "classification_fi": _json([]),
            "classification_upc": _json([]), "classification_fterm": _json([]),
        })
        tables["dwd_patent_title"].append({
            "id": f"PT{index:018d}", "patent_id": patent_id,
            "titles": _json({"zh": title_zh, "en": title_en}), "title_localized": title_en,
            "title_zh": title_zh, "db_source": SOURCE, "create_time": now, "update_time": now,
        })

    nodes = [_industry_node(index) for index in range(1, INDUSTRY_NODE_COUNT + 1)]
    for index, node in enumerate(nodes, 1):
        tables["dwd_industry_chain_info"].append({**node, "data_source": SOURCE,
                                                   "created_time": now, "updated_time": now})
        org = organizations[index - 1]
        tables["dwd_org_industry_chain_dtl"].append({
            "chain_code": node["chain_code"], "chain_name": node["chain_name"],
            "node_id": node["node_id"], "node_name": node["node_name"], "antitypic": org["org_id"],
            "credit_code": org["external_id"], "chain_score": Decimal(f"{60 + index % 40}.{index % 100:02d}"),
            "data_source": SOURCE, "created_time": now, "updated_time": now,
        })
        tables["dwd_org_industry_chain_prod_dtl"].append({
            "chain_code": node["chain_code"], "chain_name": node["chain_name"], "antitypic": org["org_id"],
            "company_name": org["name_cn"], "credit_code": org["external_id"],
            "tech_product": f"{FIELDS[(index - 1) % len(FIELDS)]}核心产品{index:04d}",
            "tech_product_seq": index % 5 + 1, "data_source": SOURCE,
            "created_time": now, "updated_time": now,
        })
        patent_row = tables["dwd_patent"][index - 1]
        title_row = tables["dwd_patent_title"][index - 1]
        tables["dwd_org_industry_chain_pat_dtl"].append({
            "chain_code": node["chain_code"], "chain_name": node["chain_name"],
            "node_id": node["node_id"], "node_name": node["node_name"],
            "apno": f"CN2026{index:08d}", "apdt": _dt(2019 + index % 7, index % 12 + 1, index % 27 + 1).date(),
            "pat_name": title_row["title_zh"], "pn": patent_row["publication_number"],
            "pbdt": _dt(2020 + index % 6, index % 12 + 1, index % 27 + 1).date(),
            "current_assign": org["name_cn"],
            "inventors": "；".join(item["name"] for item in json.loads(patent_row["inventors"])),
            "data_source": SOURCE, "created_time": now, "updated_time": now,
        })

    for index in range(1, NEWS_COUNT + 1):
        node = nodes[index - 1]
        org = organizations[index - 1]
        score = Decimal(f"{70 + index % 30}.{index % 100:02d}")
        event_date = now - timedelta(days=index % 720)
        title = f"{node['node_name']}关键技术与产业协同事件{index:04d}"
        summary = (f"事件涉及{node['chain_name']}的{node['node_name']}，影响力评分{score}，"
                   f"关联机构为{org['name_cn']}，可用于产业链节点TOP-N事件排序。")
        tables["dwd_industry_chain_news_info"].append({
            "chain_code": node["chain_code"], "chain_name": node["chain_name"],
            "news_id": f"GKXREQNEWS{index:08d}", "title": title, "relaese_date": event_date,
            "summary": summary, "source": "科技产业观察", "data_source": SOURCE,
            "created_time": now, "updated_time": now,
        })
        tables["dwd_org_important_news_info"].append({
            "org_id": org["org_id"], "name_cn": org["name_cn"], "external_id": org["external_id"],
            "news_title": title, "news_date": event_date, "news_content": summary,
            "original_textlink": f"https://example.invalid/gkx-requirement/news/{index:04d}",
            "data_source": SOURCE, "created_time": now, "updated_time": now,
        })
    return tables


def validate_rows(tables: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    errors: list[str] = []
    expected_counts = {
        "dwd_org_base_info": ORG_COUNT, "dwd_scholar": SCHOLAR_COUNT,
        "dwd_scholar_research_direction": SCHOLAR_COUNT,
        "dwd_scholar_talent_flag": SCHOLAR_COUNT, "dwd_scholar_papers": PAPER_COUNT,
        "dwd_scholar_paper_relation": PAPER_COUNT * 3, "dwd_zh_project": PROJECT_COUNT,
        "dwd_zh_project_output": PROJECT_COUNT, "dwd_patent": PATENT_COUNT,
        "dwd_patent_title": PATENT_COUNT, "dwd_industry_chain_info": INDUSTRY_NODE_COUNT,
        "dwd_industry_chain_news_info": NEWS_COUNT, "dwd_org_industry_chain_dtl": ORG_COUNT,
        "dwd_org_industry_chain_pat_dtl": PATENT_COUNT,
        "dwd_org_industry_chain_prod_dtl": ORG_COUNT, "dwd_org_important_news_info": NEWS_COUNT,
    }
    for table, rows in tables.items():
        if table in expected_counts and len(rows) != expected_counts[table]:
            errors.append(f"{table}: expected {expected_counts[table]}, got {len(rows)}")
        required = set(TABLE_COLUMNS[table])
        for row_number, row in enumerate(rows, 1):
            if set(row) != required:
                errors.append(f"{table} row {row_number}: columns differ")
                break
    scholar_ids = {row["scholar_id"] for row in tables["dwd_scholar"]}
    scholar_names = {row["name_zh"] for row in tables["dwd_scholar"]}
    paper_ids = {row["id"] for row in tables["dwd_scholar_papers"]}
    org_ids = {row["org_id"] for row in tables["dwd_org_base_info"]}
    patent_ids = {row["patent_id"] for row in tables["dwd_patent"]}
    node_ids = {row["node_id"] for row in tables["dwd_industry_chain_info"]}
    if len(scholar_ids) != SCHOLAR_COUNT:
        errors.append("dwd_scholar: duplicate scholar_id")
    if len(scholar_names) != SCHOLAR_COUNT:
        errors.append("dwd_scholar: duplicate name_zh would make name-based relations ambiguous")
    if len(paper_ids) != PAPER_COUNT:
        errors.append("dwd_scholar_papers: duplicate id")
    if len(org_ids) != ORG_COUNT:
        errors.append("dwd_org_base_info: duplicate org_id")
    if len(patent_ids) != PATENT_COUNT:
        errors.append("dwd_patent: duplicate patent_id")
    if any(row["scholar_id"] not in scholar_ids or row["related_paper_id"] not in paper_ids
           for row in tables["dwd_scholar_paper_relation"]):
        errors.append("dwd_scholar_paper_relation: orphan scholar or paper")
    if any(row["scholar_id"] not in scholar_ids or row["co_scholar_id"] not in scholar_ids
           for row in tables["dwd_scholar_coauthor"]):
        errors.append("dwd_scholar_coauthor: orphan scholar")
    if any(row["antitypic"] not in org_ids or row["node_id"] not in node_ids
           for row in tables["dwd_org_industry_chain_dtl"]):
        errors.append("dwd_org_industry_chain_dtl: orphan organization or node")
    for table, json_columns in {
        "dwd_zh_project": ("participating_institution", "participants", "keywords"),
        "dwd_zh_project_output": tuple(column for column in TABLE_COLUMNS["dwd_zh_project_output"] if column.startswith("output_")),
        "dwd_patent": ("publication_reference", "application_reference", "priority_filings", "applicants",
                       "assignees", "inventors", "keywords", "claims", "description", "figures", "language",
                       "agents", "agency", "examiners", "related_documents", "classification_loc",
                       "classification_fi", "classification_upc", "classification_fterm"),
        "dwd_patent_title": ("titles",),
    }.items():
        for row_number, row in enumerate(tables[table], 1):
            for column in json_columns:
                if row[column] is not None:
                    try:
                        json.loads(row[column])
                    except (TypeError, json.JSONDecodeError):
                        errors.append(f"{table} row {row_number}: invalid JSON in {column}")
    if any(sum(row[column] or 0 for column in (
        "journal_articles_count", "conference_papers_count", "degree_papers_count", "patents_count",
        "books_count", "awards_count", "reports_count", "other_outputs_count")) != row["total_outputs"]
        for row in tables["dwd_zh_project_output"]):
        errors.append("dwd_zh_project_output: total_outputs mismatch")
    return {"valid": not errors, "errors": errors,
            "counts": {table: len(rows) for table, rows in tables.items()},
            "relationship_coverage": {
                "coauthor_rows": len(tables["dwd_scholar_coauthor"]),
                "paper_authorship_rows": len(tables["dwd_scholar_paper_relation"]),
                "colleague_groups": len({row["work_experience_institution_zh"] for row in tables["dwd_scholar"]}),
                "alumni_groups": len({row["education_background_institution_zh"] for row in tables["dwd_scholar"]}),
                "enterprise_chain_links": len(tables["dwd_org_industry_chain_dtl"]),
                "industry_events": len(tables["dwd_industry_chain_news_info"]),
            }}


def _marker_queries() -> dict[str, tuple[str, tuple[Any, ...]]]:
    return {
        "dwd_org_base_info": ("org_id LIKE %s", ("GKXREQORG%",)),
        "dwd_scholar": ("scholar_id LIKE %s", ("GKXREQSCH%",)),
        "dwd_scholar_research_direction": ("scholar_id LIKE %s", ("GKXREQSCH%",)),
        "dwd_scholar_talent_flag": ("scholar_id LIKE %s", ("GKXREQSCH%",)),
        "dwd_scholar_papers": ("id BETWEEN %s AND %s", (PAPER_ID_BASE + 1, PAPER_ID_BASE + PAPER_COUNT)),
        "dwd_scholar_paper_relation": ("related_paper_id BETWEEN %s AND %s", (PAPER_ID_BASE + 1, PAPER_ID_BASE + PAPER_COUNT)),
        "dwd_scholar_coauthor": ("scholar_id LIKE %s", ("GKXREQSCH%",)),
        "dwd_zh_project": ("id LIKE %s", ("GKXREQ-PROJ-%",)),
        "dwd_zh_project_output": ("id LIKE %s", ("GKXREQ-PROJ-%",)),
        "dwd_patent": ("patent_id LIKE %s", ("GKXREQPAT%",)),
        "dwd_patent_title": ("patent_id LIKE %s", ("GKXREQPAT%",)),
        "dwd_industry_chain_info": ("chain_code LIKE %s", ("GKXREQCHAIN%",)),
        "dwd_industry_chain_news_info": ("data_source = %s", (SOURCE,)),
        "dwd_org_industry_chain_dtl": ("data_source = %s", (SOURCE,)),
        "dwd_org_industry_chain_pat_dtl": ("data_source = %s", (SOURCE,)),
        "dwd_org_industry_chain_prod_dtl": ("data_source = %s", (SOURCE,)),
        "dwd_org_important_news_info": ("data_source = %s", (SOURCE,)),
    }


def _marker_counts(cursor: Any) -> dict[str, int]:
    counts = {}
    for table, (where, params) in _marker_queries().items():
        cursor.execute(f"SELECT COUNT(*) AS total FROM `{table}` WHERE {where}", params)
        row = cursor.fetchone()
        counts[table] = int(next(iter(row.values())) if isinstance(row, dict) else row[0])
    return counts


def _check_schema(cursor: Any) -> None:
    for table, expected in TABLE_COLUMNS.items():
        cursor.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = %s
            ORDER BY ordinal_position
        """, (table,))
        actual = {next(iter(row.values())) if isinstance(row, dict) else row[0]
                  for row in cursor.fetchall()}
        missing = set(expected) - actual
        if missing:
            raise ValueError(f"{table}: target schema missing columns {sorted(missing)}")


def import_rows(tables: dict[str, list[dict[str, Any]]], database: str, batch_size: int) -> dict[str, int]:
    if database != "gkx":
        raise ValueError("此脚本只允许显式写入 gkx")
    settings = Settings.from_env()
    if not settings.mysql_password:
        raise ValueError("MYSQL_PASSWORD 未配置")
    try:
        import pymysql
    except ImportError as exc:
        raise RuntimeError("请安装 PyMySQL") from exc
    connection = pymysql.connect(
        host=settings.mysql_host, port=settings.mysql_port, user=settings.mysql_user,
        password=settings.mysql_password, database=database, charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor, autocommit=False, read_timeout=60, write_timeout=60,
    )
    try:
        with connection.cursor() as cursor:
            _check_schema(cursor)
            before = _marker_counts(cursor)
            occupied = {table: count for table, count in before.items() if count}
            if occupied:
                raise ValueError(f"拒绝重复导入，已存在同版本标记数据: {occupied}")
            for table, rows in tables.items():
                columns = TABLE_COLUMNS[table]
                column_sql = ", ".join(f"`{column}`" for column in columns)
                placeholders = ", ".join(["%s"] * len(columns))
                statement = f"INSERT INTO `{table}` ({column_sql}) VALUES ({placeholders})"
                for offset in range(0, len(rows), max(1, batch_size)):
                    batch = rows[offset:offset + max(1, batch_size)]
                    cursor.executemany(statement, [tuple(row[column] for column in columns) for row in batch])
            after = _marker_counts(cursor)
            expected = {table: len(rows) for table, rows in tables.items()}
            if after != expected:
                raise ValueError(f"事务内计数校验失败: expected={expected}, actual={after}")
        connection.commit()
        return after
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="按需求文档生成并事务化导入千级 gkx 演示数据；默认只预览")
    parser.add_argument("--database", default="gkx")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-database")
    args = parser.parse_args()
    tables = generate_rows()
    quality = validate_rows(tables)
    preview = {"database": args.database, "source_marker": SOURCE, "dry_run": not args.apply, **quality}
    print(json.dumps(preview, ensure_ascii=False, indent=2, default=str))
    if not quality["valid"]:
        raise SystemExit(1)
    if not args.apply:
        return
    if args.database != "gkx" or args.confirm_database != args.database:
        raise SystemExit("拒绝写入：必须使用 --database gkx --confirm-database gkx")
    inserted = import_rows(tables, args.database, args.batch_size)
    print(json.dumps({"applied": True, "database": args.database, "inserted": inserted},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
