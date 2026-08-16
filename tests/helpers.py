import time


def wait_for_run(client, run_id: str, expected: set[str] | None = None, timeout: float = 5.0) -> dict:
    expected = expected or {"COMPLETED", "FAILED", "NEED_USER_SELECTION"}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/queries/{run_id}")
        if response.status_code == 200 and response.json()["status"] in expected:
            return response.json()
        time.sleep(0.01)
    raise AssertionError(f"run {run_id} 未在 {timeout}s 内进入 {expected}")
