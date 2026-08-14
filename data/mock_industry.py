"""产业链 Mock 数据。"""
CHAINS = {
    "chain_ai": {"chain_id": "chain_ai", "name": "人工智能产业链", "nodes": ["node_compute", "node_model", "node_app"]},
}

CHAIN_NODES = {
    "node_compute": {"node_id": "node_compute", "name": "算力基础设施", "level": "上游", "company_ids": ["company_001"]},
    "node_model": {"node_id": "node_model", "name": "大模型与知识图谱", "level": "中游", "company_ids": ["company_001"]},
    "node_app": {"node_id": "node_app", "name": "行业应用", "level": "下游", "company_ids": ["company_001"]},
}

NODE_EVENTS = [
    {"event_id": "event_001", "node_id": "node_model", "title": "知识图谱平台发布", "date": "2025-03-10", "importance": 92, "evidence_id": "ev_event_001"},
    {"event_id": "event_002", "node_id": "node_model", "title": "联合实验室成立", "date": "2024-09-01", "importance": 85, "evidence_id": "ev_event_002"},
    {"event_id": "event_003", "node_id": "node_model", "title": "行业标准研讨会", "date": "2024-05-20", "importance": 70, "evidence_id": "ev_event_003"},
]
