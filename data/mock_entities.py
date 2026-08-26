"""故意包含重名专家，用于演示实体消歧。"""
MOCK_ENTITIES = [
    {"entity_id": "person_zw_001", "name": "张伟", "organization": "清华大学", "title": "计算机系教授"},
    {"entity_id": "person_zw_002", "name": "张伟", "organization": "北京理工大学", "title": "材料学院研究员"},
    {"entity_id": "person_lm_001", "name": "李明", "organization": "清华大学", "title": "人工智能研究院副教授"},
    {"entity_id": "person_lm_002", "name": "李明", "organization": "中科院自动化所", "title": "模式识别研究员"},
    # 唯一姓名用于实体自动确认 Precision 的离线回归；重名样本继续覆盖人工消歧路径。
    {"entity_id": "person_wf_001", "name": "王芳", "organization": "浙江大学", "title": "计算机学院教授"},
    {"entity_id": "person_zq_001", "name": "赵强", "organization": "上海交通大学", "title": "电子信息学院教授"},
    {"entity_id": "person_cc_001", "name": "陈晨", "organization": "复旦大学", "title": "研究员"},
    {"entity_id": "person_ly_001", "name": "刘洋", "organization": "南京大学", "title": "副教授"},
]
