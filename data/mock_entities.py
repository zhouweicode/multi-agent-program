"""故意包含重名专家，用于演示实体消歧。"""
MOCK_ENTITIES = [
    {"entity_id": "person_zw_001", "name": "张伟", "organization": "清华大学", "title": "计算机系教授"},
    {"entity_id": "person_zw_002", "name": "张伟", "organization": "北京理工大学", "title": "材料学院研究员"},
    {"entity_id": "person_lm_001", "name": "李明", "organization": "清华大学", "title": "人工智能研究院副教授"},
    {"entity_id": "person_lm_002", "name": "李明", "organization": "中科院自动化所", "title": "模式识别研究员"},
]

