"""企业关系 Mock 数据。"""
COMPANIES = [
    {"company_id": "company_001", "name": "智图科技", "industry": "人工智能"},
    {"company_id": "company_002", "name": "先进材料公司", "industry": "新材料"},
]

PERSON_COMPANY_ROLES = [
    {"entity_id": "person_zw_001", "company_id": "company_001", "role": "技术顾问", "start_year": 2020, "evidence_id": "ev_role_001"},
    {"entity_id": "person_lm_001", "company_id": "company_001", "role": "联合实验室负责人", "start_year": 2021, "evidence_id": "ev_role_002"},
]

COMPANY_PROJECTS = [
    {"project_id": "company_project_001", "company_id": "company_001", "name": "产业知识图谱平台", "participant_ids": ["person_zw_001", "person_lm_001"], "evidence_id": "ev_company_project_001"},
]

COMPANY_PATENTS = [
    {"patent_id": "company_patent_001", "company_id": "company_001", "title": "一种混合检索方法", "inventor_ids": ["person_zw_001", "person_lm_001"], "evidence_id": "ev_company_patent_001"},
]
