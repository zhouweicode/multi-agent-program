"""Black-box smoke test for health, query, optional disambiguation and final evidence."""
from __future__ import annotations

import argparse
import json
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


TERMINAL = {"COMPLETED", "FAILED", "NEED_USER_SELECTION", "ENTITY_NOT_FOUND", "CANCELLED", "TIMED_OUT"}


def request_json(base_url: str, path: str, method: str = "GET", payload: dict | None = None) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    request = Request(f"{base_url.rstrip('/')}{path}", data=data, method=method,
                      headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=15) as response:
            return json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: {exc.read().decode(errors='replace')}") from exc


def wait_for_terminal(base_url: str, run_id: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = request_json(base_url, f"/queries/{run_id}")
        if result.get("status") in TERMINAL:
            return result
        time.sleep(0.5)
    raise TimeoutError(f"query did not finish in {timeout}s: {run_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="在线查询黑盒验收")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--question", default="南京科技大学042的何伟发表过哪些论文？")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--auto-select-first", action="store_true")
    args = parser.parse_args()

    health = request_json(args.base_url, "/health/dependencies")
    if health.get("status") != "ok":
        raise RuntimeError(f"dependencies are not ready: {health}")
    run_id = f"smoke-{uuid4().hex}"
    request_json(args.base_url, "/queries", "POST",
                 {"question": args.question, "thread_id": run_id, "max_replans": 1})
    result = wait_for_terminal(args.base_url, run_id, args.timeout)
    if result["status"] == "NEED_USER_SELECTION" and args.auto_select_first:
        candidates = result.get("interrupt", {}).get("candidates", {})
        selections = {mention: rows[0]["entity_id"] for mention, rows in candidates.items() if rows}
        request_json(args.base_url, f"/queries/{run_id}/resume", "POST", {"selections": selections})
        result = wait_for_terminal(args.base_url, run_id, args.timeout)
    state = result.get("state") or {}
    summary = {
        "run_id": run_id, "status": result["status"],
        "resolved_entities": state.get("resolved_entities", {}),
        "evidence_count": len(state.get("evidence", [])),
        "has_final_answer": bool(result.get("final_answer")),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if result["status"] not in {"COMPLETED", "NEED_USER_SELECTION"}:
        raise RuntimeError(json.dumps(result.get("error"), ensure_ascii=False))
    if result["status"] == "COMPLETED" and (not result.get("final_answer") or not state.get("evidence")):
        raise RuntimeError("query completed without final answer or evidence")


if __name__ == "__main__":
    main()
