def facts(result: dict | None, tool_name: str) -> list:
    values = []
    for fact in (result or {}).get("facts", []):
        if fact.get("tool") == tool_name:
            data = fact.get("data")
            values.extend(data if isinstance(data, list) else [data])
    return values


def unique(rows: list[dict], key: str) -> list[dict]:
    seen, result = set(), []
    for row in rows:
        value = row.get(key)
        if value not in seen:
            seen.add(value)
            result.append(row)
    return result
