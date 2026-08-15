from models.llm import ModelFactory
from models.schemas import RouterOutput
from nodes.router_node import router_node


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
    result = router_node({"question": "张伟发表过哪些论文？"})
    assert result["primary_domain"] == "achievement"


def test_guardrail_does_not_override_multi_domain_query(monkeypatch):
    class ComplexModel(WrongDomainModel):
        def invoke_router(self, question):
            return RouterOutput(intent="综合分析", entity_mentions=["张伟"], complexity="complex",
                                primary_domain="achievement", requires_verification=False)

    monkeypatch.setattr(ModelFactory, "structured_model", lambda: ComplexModel())
    result = router_node({"question": "综合分析张伟的论文和任职经历"})
    assert result["complexity"] == "complex"
    assert result["primary_domain"] == "achievement"
