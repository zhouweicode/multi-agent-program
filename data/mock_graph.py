"""图关系推理 Mock 数据；边为无向关系。"""
GRAPH_EDGES = [
    {"source": "person_zw_001", "target": "person_lm_001", "relation": "COAUTHOR", "weight": 0.9, "evidence_id": "ev_graph_001"},
    {"source": "person_zw_001", "target": "company_001", "relation": "ADVISOR_OF", "weight": 0.8, "evidence_id": "ev_graph_002"},
    {"source": "person_lm_001", "target": "company_001", "relation": "LEADS_LAB", "weight": 0.85, "evidence_id": "ev_graph_003"},
    {"source": "company_001", "target": "node_model", "relation": "LOCATED_IN_CHAIN", "weight": 0.7, "evidence_id": "ev_graph_004"},
]
