"""教学用跨库 ID 映射；生产环境应替换成独立 mapping 表。"""

ENTITY_ID_MAPPINGS = [
    {"canonical_id": "person_zw_001", "mock": "person_zw_001", "mysql": "450e887j", "neo4j": "SCH001"},
    {"canonical_id": "person_zw_002", "mock": "person_zw_002"},
    {"canonical_id": "person_lm_001", "mock": "person_lm_001", "neo4j": "SCH002"},
    {"canonical_id": "person_lm_002", "mock": "person_lm_002", "neo4j": "SCH003"},
]
