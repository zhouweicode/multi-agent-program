import pytest
from langgraph.types import Command

from graph.builder import build_graph
from graph.routing import (
    scheduled_agents,
    scheduled_task_instances,
    task_completion_key,
)
from models.llm import MockStructuredModel, ModelFactory
from models.schemas import PlannedTask, SupervisorPlan
from services.observability import clear_events, get_events


def _task(task_id, agent, depends_on=None):
    return {
        "task_id": task_id,
        "agent": agent,
        "goal": task_id,
        "required_fact_types": [],
        "required_entity_ids": [],
        "depends_on": depends_on or [],
    }


def test_scheduler_dispatches_dependency_waves_in_parallel_mode():
    state = {
        "plan": {"execution_mode": "parallel"},
        "replan_count": 0,
        "tasks": [
            _task("a", "talent_agent"),
            _task("b", "achievement_agent"),
            _task("c", "enterprise_agent", ["a", "b"]),
        ],
    }
    assert scheduled_agents(state) == ["talent_agent", "achievement_agent"]
    state["task_completions"] = [
        task_completion_key("a", 0),
        task_completion_key("b", 0),
    ]
    assert scheduled_agents(state) == ["enterprise_agent"]
    state["task_completions"].append(task_completion_key("c", 0))
    assert scheduled_agents(state) == "merge"


def test_scheduler_dispatches_exactly_one_ready_task_in_sequential_mode():
    state = {
        "plan": {"execution_mode": "sequential"},
        "replan_count": 1,
        "tasks": [_task("a", "talent_agent"), _task("b", "achievement_agent")],
    }
    assert scheduled_agents(state) == ["talent_agent"]
    state["task_completions"] = [task_completion_key("a", 1)]
    assert scheduled_agents(state) == ["achievement_agent"]


def test_scheduler_rejects_unknown_dependency_and_cycle():
    with pytest.raises(ValueError, match="不存在"):
        scheduled_agents(
            {"tasks": [_task("a", "talent_agent", ["missing"])], "plan": {}}
        )
    with pytest.raises(ValueError, match="存在环"):
        scheduled_agents(
            {
                "tasks": [
                    _task("a", "talent_agent", ["b"]),
                    _task("b", "achievement_agent", ["a"]),
                ],
                "plan": {"execution_mode": "parallel"},
            }
        )


def test_full_graph_executes_sequential_dependency_plan(monkeypatch):
    class SequentialPlanner(MockStructuredModel):
        def invoke_supervisor(
            self,
            question,
            resolved_entities,
            validation_result=None,
            verification_result=None,
            task_history=None,
        ):
            ids = list(resolved_entities.values())
            return SupervisorPlan(
                tasks=[
                    PlannedTask(
                        task_id="talent_first",
                        agent="talent_agent",
                        goal="查询共同任职",
                        required_entity_ids=ids,
                    ),
                    PlannedTask(
                        task_id="achievement_second",
                        agent="achievement_agent",
                        goal="查询共同科研成果",
                        required_entity_ids=ids,
                        depends_on=["talent_first"],
                    ),
                ],
                execution_mode="sequential",
                reason="先职业后科研",
            )

    planner = SequentialPlanner()
    monkeypatch.setattr(ModelFactory, "structured_model", lambda role=None: planner)
    thread_id = "scheduler-sequential-integration"
    clear_events(thread_id)
    graph = build_graph()
    config = {"configurable": {"thread_id": thread_id}}
    first = graph.invoke(
        {
            "thread_id": thread_id,
            "question": "综合分析张伟和李明的学术和职业合作关系。",
            "max_replans": 2,
            "replan_count": 0,
            "task_history": [],
        },
        config=config,
    )
    assert first["__interrupt__"]
    final = graph.invoke(
        Command(resume={"张伟": "person_zw_001", "李明": "person_lm_001"}),
        config=config,
    )
    assert final["validation_result"]["valid"] is True
    dispatches = [
        event for event in get_events(thread_id) if event["event"] == "TASKS_DISPATCHED"
    ]
    assert [event["agents"] for event in dispatches] == [
        ["talent_agent"],
        ["achievement_agent"],
    ]
    assert final["task_completions"] == ["0:talent_first", "0:achievement_second"]


def test_scheduler_keeps_duplicate_agent_task_instances_distinct():
    state = {
        "plan": {"execution_mode": "parallel"},
        "replan_count": 0,
        "question": "分别执行两个任务",
        "tasks": [
            _task("papers", "achievement_agent"),
            _task("patents", "achievement_agent"),
        ],
    }
    sends = scheduled_task_instances(state)
    assert len(sends) == 2
    assert [send.arg["active_task"]["task_id"] for send in sends] == [
        "papers", "patents",
    ]


def test_full_graph_executes_two_tasks_for_the_same_agent(monkeypatch):
    class DuplicateAgentPlanner(MockStructuredModel):
        def invoke_router(self, question):
            from models.schemas import RouterOutput
            return RouterOutput(
                intent="多任务成果查询", entity_mentions=["张伟"],
                complexity="complex", primary_domain="achievement",
            )

        def invoke_supervisor(
            self, question, resolved_entities, validation_result=None,
            verification_result=None, task_history=None, memory_context=None,
        ):
            entity_ids = list(resolved_entities.values())
            return SupervisorPlan(tasks=[
                PlannedTask(
                    task_id="papers", agent="achievement_agent",
                    goal="查询张伟论文", required_fact_types=["papers"],
                    required_entity_ids=entity_ids,
                ),
                PlannedTask(
                    task_id="patents", agent="achievement_agent",
                    goal="查询张伟专利", required_fact_types=["patents"],
                    required_entity_ids=entity_ids,
                ),
            ], execution_mode="parallel", reason="同一领域的两个独立任务")

    planner = DuplicateAgentPlanner()
    monkeypatch.setattr(ModelFactory, "structured_model", lambda role=None: planner)
    graph = build_graph()
    config = {"configurable": {"thread_id": "duplicate-agent-tasks"}}
    first = graph.invoke({
        "question": "分别处理张伟的两个独立子任务",
        "max_replans": 1, "replan_count": 0, "task_history": [],
    }, config=config)
    assert first["__interrupt__"]
    final = graph.invoke(
        Command(resume={"张伟": "person_zw_001"}), config=config
    )
    assert set(final["task_results"]) == {"0:papers", "0:patents"}
    assert final["task_results"]["0:papers"]["result"]["task_id"] == "papers"
    assert final["task_results"]["0:patents"]["result"]["task_id"] == "patents"
    assert final["validation_result"]["valid"] is True
