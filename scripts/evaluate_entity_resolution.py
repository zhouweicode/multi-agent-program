"""运行实体解析小型评测集，输出 Recall@K、Top1 与自动确认准确率。"""
import argparse
import json
from pathlib import Path

from services.resources import get_entity_service


def evaluate(path: Path) -> dict:
    cases = json.loads(path.read_text(encoding="utf-8"))
    service = get_entity_service()
    recall_hits = top1_hits = auto_total = auto_hits = not_found_hits = 0
    details = []
    for case in cases:
        rows = service.search(case["mention"], case.get("context", ""))
        expected = case.get("expected_entity_id")
        predicted = service.auto_resolve(rows)
        ids = [row["entity_id"] for row in rows]
        if expected:
            recall_hits += int(expected in ids)
            top1_hits += int(bool(ids) and ids[0] == expected)
            if predicted:
                auto_total += 1
                auto_hits += int(predicted == expected)
        else:
            not_found_hits += int(not rows and case.get("expected_status") == "ENTITY_NOT_FOUND")
        details.append({"mention": case["mention"], "expected": expected or case.get("expected_status"),
                        "top1": ids[0] if ids else None, "auto_resolved": predicted, "candidate_count": len(ids)})
    entity_cases = sum(1 for case in cases if case.get("expected_entity_id"))
    return {"case_count": len(cases), "recall_at_k": recall_hits / entity_cases if entity_cases else 0,
            "top1_accuracy": top1_hits / entity_cases if entity_cases else 0,
            "auto_resolve_accuracy": auto_hits / auto_total if auto_total else None,
            "not_found_accuracy": not_found_hits / (len(cases) - entity_cases) if len(cases) > entity_cases else None,
            "details": details}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=Path("evals/entity_resolution_cases.json"))
    args = parser.parse_args()
    print(json.dumps(evaluate(args.cases), ensure_ascii=False, indent=2))
