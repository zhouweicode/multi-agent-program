"""企业关系 Agent 独享工具。"""
from langchain_core.tools import tool
from data.mock_enterprises import COMPANIES, PERSON_COMPANY_ROLES, COMPANY_PROJECTS, COMPANY_PATENTS


def _company_name(company_id: str) -> str:
    return next((row["name"] for row in COMPANIES if row["company_id"] == company_id), company_id)


@tool
def get_person_company_roles(entity_ids: list[str]) -> list[dict]:
    """查询专家在企业中的任职、顾问等角色。"""
    wanted = set(entity_ids)
    return [{**row, "company_name": _company_name(row["company_id"])}
            for row in PERSON_COMPANY_ROLES if row["entity_id"] in wanted]


@tool
def get_company_projects(company_id: str) -> list[dict]:
    """查询企业参与的联合项目。"""
    return [{**row, "company_name": _company_name(row["company_id"])}
            for row in COMPANY_PROJECTS if row["company_id"] == company_id]


@tool
def get_company_patents(company_id: str) -> list[dict]:
    """查询企业拥有或参与的专利。"""
    return [{**row, "company_name": _company_name(row["company_id"])}
            for row in COMPANY_PATENTS if row["company_id"] == company_id]
