"""企业关系 Agent 独享工具。"""
from langchain_core.tools import tool
from services.resources import get_enterprise_service


@tool
def get_person_company_roles(entity_ids: list[str]) -> list[dict]:
    """查询专家在企业中的任职、顾问等角色。"""
    return get_enterprise_service().get_person_company_roles(entity_ids)


@tool
def get_company_projects(company_id: str) -> list[dict]:
    """查询企业参与的联合项目。"""
    return get_enterprise_service().get_company_projects(company_id)


@tool
def get_company_patents(company_id: str) -> list[dict]:
    """查询企业拥有或参与的专利。"""
    return get_enterprise_service().get_company_patents(company_id)
