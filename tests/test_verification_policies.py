from nodes.verification_node import verification_agent_node


def test_enterprise_claim_uses_generic_evidence_and_source_verification():
    evidence = {
        "evidence_id": "ev_role_001",
        "fact_type": "company_role",
        "source_type": "mock",
        "source_name": "mock:enterprise_roles",
        "source_record_id": "role-1",
        "entity_ids": ["person_zw_001"],
        "content": {"company_id": "company_001", "role": "顾问"},
        "source_tool": "get_person_company_roles",
    }
    result = verification_agent_node({
        "thread_id": "enterprise-verification-policy",
        "question": "验证张伟的企业关系是否成立",
        "verification_claim_type": "ENTERPRISE_RELATION",
        "resolved_entities": {"张伟": "person_zw_001"},
        "evidence": [evidence],
        "task_history": [],
    })["verification_result"]
    assert result["status"] == "PASS"
    assert result["claim_type"] == "ENTERPRISE_RELATION"
    assert result["relation"] == "ENTERPRISE_RELATED"
    assert [call["name"] for call in result["tool_calls"]] == [
        "verify_evidence", "check_source",
    ]
