"""演示：首次暂停并打印同名候选，再模拟用户选择后恢复。"""
import logging
from langgraph.types import Command
from graph.builder import build_graph


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")
    graph = build_graph()
    config = {"configurable": {"thread_id": "stage-1-demo"}}
    initial = {"thread_id": "stage-1-demo", "question": "综合分析张伟和李明的学术和职业合作关系。", "replan_count": 0, "max_replans": 2,
               "resolved_entities": {}, "task_history": []}
    first = graph.invoke(initial, config=config)
    interrupts = first.get("__interrupt__", ())
    if not interrupts:
        raise RuntimeError("预期发生实体消歧暂停，但未发生")
    payload = interrupts[0].value
    print("\n=== NEED_USER_SELECTION ===")
    for name, candidates in payload["candidates"].items():
        print(f"候选姓名：{name}")
        for item in candidates:
            print(f"  - {item['entity_id']} | {item['organization']} | {item['title']}")
    selection = {"张伟": "person_zw_001", "李明": "person_lm_001"}
    print(f"\n模拟用户选择：{selection}\n")
    final = graph.invoke(Command(resume=selection), config=config)
    print("=== FINAL ANSWER ===")
    print(final["final_answer"])


if __name__ == "__main__":
    main()
