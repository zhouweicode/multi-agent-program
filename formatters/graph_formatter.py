from formatters.utils import facts


def format_graph(result: dict | None, resolved: dict[str, str]) -> tuple[str | None, bool]:
    if not result:
        return None, False
    parts = []
    for path in facts(result, "find_path"):
        if path.get("found"):
            parts.append(f"发现 {path.get('hop_count')} 跳关系路径：{' → '.join(path.get('nodes', []))}")
    for strength in facts(result, "calculate_path_strength"):
        if strength.get("path", {}).get("found"):
            parts.append(f"路径关系强度为 {strength.get('strength')}")
    return ("图关系推理：" + "；".join(parts) if parts else "图关系推理：当前数据源未发现可用关系路径"), False
