"""ToolRegistry 是 Agent、Skill、Validator 与传输层的唯一契约来源。"""

import pytest

from models.contracts import (
    AGENT_DOMAINS,
    DEFAULT_REQUIRED_FACT_TYPES,
    FACT_TYPE_TO_TOOL,
)
from skills.planning import CAPABILITY_BINDINGS
from tools.contracts import AgentContract, CapabilitySpec, ToolSpec
from tools.provider import get_tools, tool_names
from tools.registry import ToolRegistry, tool_registry


def test_registry_contains_all_domain_tools_and_stable_contracts():
    assert len(tool_registry.list()) == 28
    assert set(tool_registry.agent_domains.values()) == {
        "talent",
        "achievement",
        "enterprise",
        "industry",
        "graph",
        "verification",
        "web",
    }
    assert tool_registry.fact_type_to_tool["common_papers"] == "get_common_papers"
    assert tool_registry.get("search_web").trust_level == "remote_content"
    assert tool_registry.get("search_web").open_world is True


def test_legacy_contract_exports_are_derived_from_registry():
    assert AGENT_DOMAINS == tool_registry.agent_domains
    assert FACT_TYPE_TO_TOOL == tool_registry.fact_type_to_tool
    assert DEFAULT_REQUIRED_FACT_TYPES == tool_registry.default_required_fact_types
    assert CAPABILITY_BINDINGS == {
        item.name: item for item in tool_registry.list_capabilities()
    }


def test_every_capability_fact_type_resolves_to_the_same_agent_tool():
    for capability in tool_registry.list_capabilities():
        for fact_type in capability.required_fact_types:
            tool_name = tool_registry.fact_type_to_tool[fact_type]
            assert tool_registry.get(tool_name).agent == capability.agent
            assert tool_name in tool_names(capability.domain)


def test_local_provider_preserves_agent_whitelist_and_adds_contract_metadata():
    talent = get_tools("talent")
    assert [tool.name for tool in talent] == tool_names("talent")
    assert "search_web" not in {tool.name for tool in talent}
    assert all(tool.metadata["authorized_agent"] == "talent_agent" for tool in talent)
    assert all(tool.metadata["tool_transport"] == "local" for tool in talent)


def test_registry_rejects_capability_fact_type_owned_by_another_agent():
    tool = tool_registry.get("get_person_profile")
    with pytest.raises(ValueError, match="不属于 achievement_agent"):
        ToolRegistry(
            [
                ToolSpec(
                    tool.name,
                    tool.domain,
                    tool.agent,
                    tool.implementation,
                    tool.fact_types,
                )
            ],
            [
                AgentContract("talent_agent", "talent", ()),
                AgentContract("achievement_agent", "achievement", ()),
            ],
            [
                CapabilitySpec(
                    "invalid",
                    "achievement_agent",
                    "achievement",
                    "invalid",
                    ("person_profile",),
                )
            ],
        )
