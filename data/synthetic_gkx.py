"""Deterministic synthetic research knowledge-graph dataset.

The generator intentionally creates repeated names while keeping stable source IDs. This
allows entity-resolution tests without relying on production data.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import random
from typing import Any


TABLE_ORDER = (
    "organizations",
    "departments",
    "enterprises",
    "industry_segments",
    "dwd_scholar",
    "dwd_scholar_papers",
    "dwd_scholar_paper_relation",
    "dwd_zh_project",
    "scholar_project_relation",
    "dwd_patent",
    "dwd_patent_title",
    "scholar_patent_relation",
    "scholar_enterprise_relation",
    "enterprise_industry_relation",
    "industry_events",
)


@dataclass(frozen=True)
class SyntheticConfig:
    seed: int = 20260821
    scholar_count: int = 2_000
    organization_count: int = 100
    enterprise_count: int = 300
    paper_count: int = 15_000
    project_count: int = 2_000
    patent_count: int = 5_000
    industry_segment_count: int = 100
    industry_event_count: int = 500

    def validate(self) -> None:
        for key, value in asdict(self).items():
            if key != "seed" and value < 1:
                raise ValueError(f"{key} must be positive")


SURNAMES = tuple("王李张刘陈杨黄赵吴周徐孙马朱胡郭何林高罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘于蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦傅方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严赖覃洪武莫孔")
GIVEN_NAMES = (
    "伟", "芳", "娜", "敏", "静", "强", "磊", "洋", "勇", "艳", "杰", "娟",
    "涛", "明", "超", "秀英", "霞", "平", "刚", "桂英", "博文", "子涵", "宇轩",
    "雨桐", "浩然", "思远", "嘉宁", "文昊", "若曦", "一鸣", "晓峰", "建国",
)
FIELDS = ("人工智能", "知识图谱", "生物医药", "新能源", "新材料", "机器人", "量子信息", "集成电路", "智能制造", "遥感科学")
CITIES = ("北京", "上海", "深圳", "广州", "杭州", "南京", "武汉", "西安", "成都", "合肥", "天津", "苏州")
POSITIONS = ("教授", "副教授", "研究员", "副研究员", "讲师", "高级工程师")
DEGREES = ("博士", "硕士")
ENTERPRISE_SUFFIXES = ("科技有限公司", "智能技术有限公司", "创新研究院", "产业集团", "数据技术有限公司")


def _stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _date_for(index: int) -> str:
    return (date(2018, 1, 1) + timedelta(days=index % 2920)).isoformat()


def _sample_ids(rng: random.Random, population_size: int, minimum: int, maximum: int) -> list[int]:
    size = min(population_size, rng.randint(minimum, maximum))
    return rng.sample(range(1, population_size + 1), size)


def generate_dataset(config: SyntheticConfig = SyntheticConfig()) -> dict[str, list[dict[str, Any]]]:
    config.validate()
    rng = random.Random(config.seed)
    tables: dict[str, list[dict[str, Any]]] = {name: [] for name in TABLE_ORDER}

    for index in range(1, config.organization_count + 1):
        city = CITIES[(index - 1) % len(CITIES)]
        kind = "University" if index % 4 else "ResearchInstitute"
        zh_name = f"{city}{'科技大学' if kind == 'University' else '先进技术研究院'}{index:03d}"
        tables["organizations"].append({
            "org_id": f"ORG{index:05d}", "name_zh": zh_name,
            "name_en": f"{city} Research Organization {index:03d}", "org_type": kind,
            "city": city, "status": 1, "updated_at": _date_for(index),
        })
        for dept_number, field in enumerate(FIELDS[:3], 1):
            tables["departments"].append({
                "dept_id": f"DEPT{index:05d}{dept_number:02d}", "org_id": f"ORG{index:05d}",
                "name_zh": f"{field}学院", "name_en": f"School of {field} {dept_number}", "status": 1,
            })

    for index in range(1, config.enterprise_count + 1):
        city = CITIES[(index * 3) % len(CITIES)]
        field = FIELDS[index % len(FIELDS)]
        tables["enterprises"].append({
            "enterprise_id": f"ENT{index:06d}", "name_zh": f"{city}{field}{ENTERPRISE_SUFFIXES[index % len(ENTERPRISE_SUFFIXES)]}{index:03d}",
            "name_en": f"Synthetic {field} Enterprise {index:03d}", "credit_code": f"SYN{index:015d}",
            "city": city, "status": 1, "updated_at": _date_for(index + 50),
        })

    for index in range(1, config.industry_segment_count + 1):
        field = FIELDS[index % len(FIELDS)]
        level = 1 if index <= len(FIELDS) else 2
        parent_id = None if level == 1 else f"SEG{((index - 1) % len(FIELDS)) + 1:04d}"
        tables["industry_segments"].append({
            "segment_id": f"SEG{index:04d}", "name_zh": f"{field}{'产业链' if level == 1 else '细分环节'}{index:03d}",
            "level": level, "parent_segment_id": parent_id, "status": 1,
        })

    for index in range(1, config.scholar_count + 1):
        # A deliberately small name pool creates realistic same-name resolution cases.
        surname = SURNAMES[(index - 1) % 20]
        given_name = GIVEN_NAMES[((index - 1) // 20) % 10]
        name_zh = f"{surname}{given_name}"
        # Shift every repeated-name cycle so namesakes belong to different organizations.
        name_cycle = (index - 1) // 200
        org_number = ((index * 17 + name_cycle * 13) % config.organization_count) + 1
        org = tables["organizations"][org_number - 1]
        dept_number = (index % 3) + 1
        start_year = 1998 + index % 23
        tables["dwd_scholar"].append({
            "scholar_id": f"SCH{index:07d}", "name_zh": name_zh,
            "name_en": f"{given_name} {surname}",
            "org_id": org["org_id"], "dept_id": f"DEPT{org_number:05d}{dept_number:02d}",
            "scholar_org_name_zh": org["name_zh"], "scholar_org_name_en": org["name_en"],
            "work_experience_date": f"{start_year}-至今", "work_experience_institution_zh": org["name_zh"],
            "work_experience_institution_en": org["name_en"], "work_experience_department_zh": f"{FIELDS[dept_number - 1]}学院",
            "work_experience_department_en": f"School of {FIELDS[dept_number - 1]}",
            "work_experience_position_zh": POSITIONS[index % len(POSITIONS)], "work_experience_position_en": "Research Faculty",
            "education_background_date": f"{start_year - 8}-{start_year - 3}",
            "education_background_institution_zh": tables["organizations"][(org_number + 6) % config.organization_count]["name_zh"],
            "education_background_institution_en": tables["organizations"][(org_number + 6) % config.organization_count]["name_en"],
            "education_background_degree_zh": DEGREES[index % len(DEGREES)], "education_background_degree_en": "PhD" if index % 2 else "Master",
            "orcid": f"0000-0002-{index // 10000:04d}-{index % 10000:04d}",
            "email_hash": _stable_hash(f"scholar-{index}@synthetic.invalid"), "research_field": FIELDS[index % len(FIELDS)],
            "status": 1, "updated_at": _date_for(index),
        })

    for index in range(1, config.paper_count + 1):
        year = 2010 + index % 17
        field = FIELDS[index % len(FIELDS)]
        paper_id = f"PAP{index:08d}"
        tables["dwd_scholar_papers"].append({
            "id": paper_id, "zh_name": f"{field}关键技术与应用研究{index:05d}",
            "en_name": f"Research on Synthetic {field} Technology {index:05d}",
            "doi": f"10.9999/synthetic.{index:08d}", "cover_date_start": f"{year}-01-01",
            "venue": f"合成科研期刊{index % 30 + 1}", "status": 1, "updated_at": _date_for(index),
        })
        for order, scholar_number in enumerate(_sample_ids(rng, config.scholar_count, 1, 5), 1):
            tables["dwd_scholar_paper_relation"].append({
                "id": f"AUT{index:08d}{order:02d}", "scholar_id": f"SCH{scholar_number:07d}",
                "related_paper_id": paper_id, "author_order": order, "year": year,
                "publish_time": f"{year}-01-01", "status": 1,
                "evidence_id": f"syn_paper_{paper_id}_{scholar_number}",
            })

    for index in range(1, config.project_count + 1):
        participant_numbers = _sample_ids(rng, config.scholar_count, 2, 6)
        participants = [tables["dwd_scholar"][number - 1]["name_zh"] for number in participant_numbers]
        project_id = f"PROJ{index:07d}"
        year = 2015 + index % 12
        tables["dwd_zh_project"].append({
            "id": project_id, "title": f"{FIELDS[index % len(FIELDS)]}重点研发项目{index:04d}",
            "approval_year": year, "research_period": f"{year}-{year + 3}",
            "project_host": participants[0], "participants": json.dumps(participants, ensure_ascii=False),
            "status": 1, "updated_at": _date_for(index + 100),
        })
        for order, scholar_number in enumerate(participant_numbers, 1):
            tables["scholar_project_relation"].append({
                "id": f"PPR{index:07d}{order:02d}", "project_id": project_id,
                "scholar_id": f"SCH{scholar_number:07d}", "role": "HOST" if order == 1 else "PARTICIPANT",
                "status": 1, "evidence_id": f"syn_project_{project_id}_{scholar_number}",
            })

    for index in range(1, config.patent_count + 1):
        inventor_numbers = _sample_ids(rng, config.scholar_count, 1, 4)
        inventors = [{"scholar_id": f"SCH{number:07d}", "name": tables["dwd_scholar"][number - 1]["name_zh"]} for number in inventor_numbers]
        patent_id = f"PAT{index:08d}"
        enterprise_id = f"ENT{((index * 11) % config.enterprise_count) + 1:06d}"
        tables["dwd_patent"].append({
            "patent_id": patent_id, "publication_number": f"CN{202000000000 + index}A",
            "inventors": json.dumps(inventors, ensure_ascii=False), "assignee_enterprise_id": enterprise_id,
            "application_date": _date_for(index + 300), "status": 1, "updated_at": _date_for(index + 301),
        })
        tables["dwd_patent_title"].append({
            "patent_id": patent_id, "title_zh": f"一种用于{FIELDS[index % len(FIELDS)]}的合成装置及方法{index:05d}",
            "title_localized": f"Synthetic patent method {index:05d}", "status": 1,
        })
        for order, scholar_number in enumerate(inventor_numbers, 1):
            tables["scholar_patent_relation"].append({
                "id": f"IPR{index:08d}{order:02d}", "patent_id": patent_id,
                "scholar_id": f"SCH{scholar_number:07d}", "inventor_order": order, "status": 1,
                "evidence_id": f"syn_patent_{patent_id}_{scholar_number}",
            })

    role_count = max(1, config.scholar_count // 5)
    for index in range(1, role_count + 1):
        scholar_number = ((index * 29) % config.scholar_count) + 1
        enterprise_number = ((index * 31) % config.enterprise_count) + 1
        tables["scholar_enterprise_relation"].append({
            "id": f"SER{index:07d}", "scholar_id": f"SCH{scholar_number:07d}",
            "enterprise_id": f"ENT{enterprise_number:06d}", "role": ("FOUNDER", "ADVISOR", "SCIENTIST")[index % 3],
            "start_year": 2016 + index % 10, "status": 1, "evidence_id": f"syn_enterprise_role_{index}",
        })

    for index in range(1, config.enterprise_count + 1):
        segment_number = ((index * 7) % config.industry_segment_count) + 1
        tables["enterprise_industry_relation"].append({
            "id": f"EIR{index:07d}", "enterprise_id": f"ENT{index:06d}",
            "segment_id": f"SEG{segment_number:04d}", "relation_type": "BELONGS_TO", "status": 1,
        })

    for index in range(1, config.industry_event_count + 1):
        segment_number = ((index * 13) % config.industry_segment_count) + 1
        tables["industry_events"].append({
            "event_id": f"EVT{index:07d}", "segment_id": f"SEG{segment_number:04d}",
            "title": f"合成产业事件{index:05d}", "event_date": _date_for(index + 700),
            "importance": round(0.5 + (index % 50) / 100, 2), "status": 1,
            "evidence_id": f"syn_industry_event_{index}",
        })
    # Every dataset participates in the same (updated_at, primary_key) incremental protocol.
    for table_offset, table_name in enumerate(TABLE_ORDER):
        for row_offset, row in enumerate(tables[table_name], 1):
            row.setdefault("updated_at", _date_for(row_offset + table_offset * 37))
    return tables


def write_dataset(output_dir: Path, config: SyntheticConfig = SyntheticConfig()) -> dict[str, Any]:
    tables = generate_dataset(config)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {"synthetic": True, "config": asdict(config), "tables": {}}
    for table_name in TABLE_ORDER:
        path = output_dir / f"{table_name}.jsonl"
        digest = hashlib.sha256()
        with path.open("w", encoding="utf-8") as handle:
            for row in tables[table_name]:
                line = json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                handle.write(line)
                digest.update(line.encode("utf-8"))
        manifest["tables"][table_name] = {"rows": len(tables[table_name]), "sha256": digest.hexdigest()}
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def read_dataset(input_dir: Path) -> dict[str, list[dict[str, Any]]]:
    tables: dict[str, list[dict[str, Any]]] = {}
    for table_name in TABLE_ORDER:
        path = input_dir / f"{table_name}.jsonl"
        if not path.is_file():
            raise FileNotFoundError(f"missing synthetic table: {path}")
        with path.open(encoding="utf-8") as handle:
            tables[table_name] = [json.loads(line) for line in handle if line.strip()]
    return tables
