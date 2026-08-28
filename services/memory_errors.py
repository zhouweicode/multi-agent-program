"""Typed memory lifecycle errors exposed consistently by storage adapters."""


class MemoryRevisionConflict(RuntimeError):
    def __init__(self, fact_id: str, expected: int, actual: int):
        self.fact_id = fact_id
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"记忆已被其他操作修改：期望 revision={expected}，当前 revision={actual}"
        )
