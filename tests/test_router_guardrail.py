from models.llm import ModelFactory
from models.schemas import RouterOutput
from nodes.router_node import router_node


class NoAuthoritativeMentions:
    def mentions_in_text(self, text):
        return []


class WrongDomainModel:
    """模拟真实模型返回合法 JSON、但业务领域判断错误。"""

    def invoke_router(self, question):
        return RouterOutput(
            intent="查询专家信息",
            entity_mentions=["张伟"],
            complexity="simple",
            primary_domain="talent",
            requires_verification=False,
        )


def test_paper_keyword_corrects_wrong_llm_domain(monkeypatch):
    monkeypatch.setattr(ModelFactory, "structured_model", lambda: WrongDomainModel())
    monkeypatch.setattr("nodes.router_node.get_entity_service", lambda: NoAuthoritativeMentions())
    result = router_node({"question": "张伟发表过哪些论文？"})
    assert result["primary_domain"] == "achievement"


def test_guardrail_does_not_override_multi_domain_query(monkeypatch):
    class ComplexModel(WrongDomainModel):
        def invoke_router(self, question):
            return RouterOutput(intent="综合分析", entity_mentions=["张伟"], complexity="complex",
                                primary_domain="achievement", requires_verification=False)

    monkeypatch.setattr(ModelFactory, "structured_model", lambda: ComplexModel())
    monkeypatch.setattr("nodes.router_node.get_entity_service", lambda: NoAuthoritativeMentions())
    result = router_node({"question": "综合分析张伟的论文和任职经历"})
    assert result["complexity"] == "complex"
    assert result["primary_domain"] == "achievement"


def test_authoritative_mentions_replace_organization_misclassification(monkeypatch):
    class Misclassified(WrongDomainModel):
        def invoke_router(self, question):
            return RouterOutput(intent="论文查询", entity_mentions=["南京科技大学042"], complexity="simple",
                                primary_domain="achievement", requires_verification=False)

    class RealMentions:
        def mentions_in_text(self, text):
            return ["何伟"]

    monkeypatch.setattr(ModelFactory, "structured_model", lambda: Misclassified())
    monkeypatch.setattr("nodes.router_node.get_entity_service", lambda: RealMentions())
    result = router_node({"question": "南京科技大学042的何伟发表过哪些论文？"})
    assert result["entity_mentions"] == ["何伟"]


def test_authoritative_mentions_do_not_drop_coordinated_unknown_person(monkeypatch):
    class IncompleteModel(WrongDomainModel):
        def invoke_router(self, question):
            return RouterOutput(intent="综合分析", entity_mentions=["张伟"], complexity="complex",
                                primary_domain="graph", requires_verification=True)

    class PartialRealMentions:
        def mentions_in_text(self, text):
            return ["张伟"]

    monkeypatch.setattr(ModelFactory, "structured_model", lambda: IncompleteModel())
    monkeypatch.setattr("nodes.router_node.get_entity_service", lambda: PartialRealMentions())
    result = router_node({"question": "综合分析张伟和李明的学术、职业和企业合作关系。"})
    assert result["entity_mentions"] == ["张伟", "李明"]


def test_factual_web_query_cannot_be_misrouted_to_relation_verification(monkeypatch):
    class OverVerifyingModel(WrongDomainModel):
        def invoke_router(self, question):
            return RouterOutput(intent="联网事实查询", entity_mentions=[], complexity="simple",
                                primary_domain="web", requires_verification=True)

    monkeypatch.setattr(ModelFactory, "structured_model", lambda: OverVerifyingModel())
    monkeypatch.setattr("nodes.router_node.get_entity_service", lambda: NoAuthoritativeMentions())
    result = router_node({"question": "清华大学是什么时候成立的？", "web_search_enabled": True})
    assert result["primary_domain"] == "web"
    assert result["requires_verification"] is False


def test_claim_specific_verification_guardrail_sets_enterprise_policy(monkeypatch):
    monkeypatch.setattr(ModelFactory, "structured_model", lambda: WrongDomainModel())
    monkeypatch.setattr("nodes.router_node.get_entity_service", lambda: NoAuthoritativeMentions())
    result = router_node({"question": "验证张伟和李明的企业关系是否成立"})
    assert result["primary_domain"] == "enterprise"
    assert result["requires_verification"] is True
    assert result["verification_claim_type"] == "ENTERPRISE_RELATION"
