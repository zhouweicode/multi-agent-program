"""Neo4j 只读图仓储，对外返回与原 Mock 图工具兼容的数据结构。"""
from __future__ import annotations

from math import prod
from typing import Any

from models.settings import Settings


def _id_expression(variable: str) -> str:
    """按标签读取节点主键，避免把外键（如 IndustryEvent.segment_id）误当节点 ID。"""
    mapping = (
        ("Scholar", "scholar_id"), ("Organization", "org_id"), ("Department", "dept_id"),
        ("Paper", "paper_id"), ("Patent", "patent_id"), ("Project", "project_id"),
        ("Enterprise", "enterprise_id"), ("School", "school_id"), ("College", "college_id"),
        ("Technology", "tech_id"), ("IndustrySegment", "segment_id"), ("Team", "team_id"),
        ("IndustryEvent", "event_id"), ("CapitalEvent", "capital_event_id"), ("Award", "award_id"),
        ("UpdateBatch", "batch_id"), ("Authorship", "authorship_id"),
        ("Inventorship", "inventorship_id"), ("ProjectParticipation", "participation_id"),
        ("Employment", "employment_id"), ("Education", "education_id"),
        ("EnterpriseCooperation", "coop_id"), ("InferencePath", "path_id"),
        ("TrendReport", "report_id"), ("Venue", "venue_id"),
    )
    clauses = " ".join(f"WHEN {variable}:{label} THEN {variable}.{key}" for label, key in mapping)
    return f"CASE {clauses} ELSE null END"


def _priority_expression(variable: str) -> str:
    """跨标签 ID 冲突时优先业务入口最常用的 Scholar，再按标签稳定排序。"""
    labels = ("Scholar", "Enterprise", "IndustrySegment", "Technology", "Organization", "Paper",
              "Patent", "Project", "School", "College", "Department", "Team", "IndustryEvent")
    clauses = " ".join(f"WHEN {variable}:{label} THEN {index}" for index, label in enumerate(labels))
    return f"CASE {clauses} ELSE 99 END"


def _json_value(value: Any) -> Any:
    """把 Neo4j temporal 等值转换为 FastAPI 可序列化值。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    return str(value)


class Neo4jGraphRepository:
    backend = "neo4j"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_env()
        if not self.settings.neo4j_password:
            raise ValueError("NEO4J_PASSWORD 未配置")
        try:
            from neo4j import GraphDatabase
        except ImportError as exc:
            raise RuntimeError("请安装 neo4j Python Driver") from exc
        self.driver = GraphDatabase.driver(
            self.settings.neo4j_uri,
            auth=(self.settings.neo4j_user, self.settings.neo4j_password),
        )

    def close(self) -> None:
        self.driver.close()

    def health(self) -> dict:
        self.driver.verify_connectivity()
        return {"backend": "neo4j", "ready": True, "database": self.settings.neo4j_database}

    def _read(self, query: str, **parameters: Any) -> list[dict]:
        with self.driver.session(database=self.settings.neo4j_database) as session:
            records = session.execute_read(lambda tx: list(tx.run(query, **parameters)))
        return [{key: _json_value(record[key]) for key in record.keys()} for record in records]

    @staticmethod
    def _node_id(node: Any) -> str | None:
        for key in ("entity_id", "scholar_id", "org_id", "dept_id", "paper_id", "patent_id",
                    "project_id", "enterprise_id", "school_id", "college_id", "tech_id",
                    "segment_id", "team_id", "event_id", "capital_event_id", "award_id",
                    "batch_id", "authorship_id", "inventorship_id", "participation_id",
                    "employment_id", "education_id", "coop_id", "path_id", "report_id"):
            if node.get(key) is not None:
                return str(node[key])
        return None

    def get_neighbors(self, entity_id: str) -> list[dict]:
        node_id = _id_expression("n")
        other_id = _id_expression("m")
        query = f"""
            MATCH (n) WHERE {node_id} = $entity_id
            WITH n ORDER BY {_priority_expression('n')} LIMIT 1
            MATCH (n)-[r]-(m)
            RETURN {other_id} AS entity_id, labels(m) AS labels,
                   type(r) AS relation, properties(r) AS properties,
                   {node_id} AS source, {other_id} AS target
            ORDER BY relation, entity_id
        """
        rows = self._read(query, entity_id=entity_id)
        for row in rows:
            row["weight"] = float(row["properties"].get("weight", row["properties"].get("confidence", 1.0)))
        return rows

    def find_path(self, source_id: str, target_id: str, max_hops: int = 4) -> dict:
        hops = max(1, min(int(max_hops), 8))
        source_expression = _id_expression("s")
        target_expression = _id_expression("t")
        query = f"""
            MATCH (s) WHERE {source_expression} = $source_id
            WITH s ORDER BY {_priority_expression('s')} LIMIT 1
            MATCH (t) WHERE {target_expression} = $target_id
            WITH s, t ORDER BY {_priority_expression('t')} LIMIT 1
            MATCH p = shortestPath((s)-[*..{hops}]-(t))
            RETURN p
            LIMIT 1
        """
        def load_path(tx):
            record = tx.run(query, source_id=source_id, target_id=target_id).single()
            if record is None:
                return None
            path = record["p"]
            nodes = [self._node_id(node) for node in path.nodes]
            edges = []
            for relationship in path.relationships:
                properties = dict(relationship)
                edges.append({
                    "source": self._node_id(relationship.start_node),
                    "target": self._node_id(relationship.end_node),
                    "relation": relationship.type,
                    "weight": float(properties.get("weight", properties.get("confidence", properties.get("strength_score", properties.get("importance", 1.0))))),
                    "properties": _json_value(properties),
                })
            return {"nodes": nodes, "edges": edges}

        with self.driver.session(database=self.settings.neo4j_database) as session:
            row = session.execute_read(load_path)
        if row is None:
            return {"found": False, "nodes": [], "edges": [], "hop_count": 0}
        return {"found": True, "nodes": row["nodes"], "edges": row["edges"], "hop_count": len(row["edges"])}

    def k_hop_expand(self, entity_id: str, k: int = 2) -> dict:
        depth = max(1, min(int(k), 5))
        levels = []
        start_expression = _id_expression("s")
        end_expression = _id_expression("m")
        for hop in range(1, depth + 1):
            query = f"""
                MATCH (s) WHERE {start_expression} = $entity_id
                WITH s ORDER BY {_priority_expression('s')} LIMIT 1
                MATCH (s)-[*{hop}]-(m) WHERE {end_expression} IS NOT NULL
                RETURN DISTINCT {end_expression} AS entity_id ORDER BY entity_id
            """
            current = {row["entity_id"] for row in self._read(query, entity_id=entity_id)}
            previous = {item for level in levels for item in level["entity_ids"]}
            levels.append({"hop": hop, "entity_ids": sorted(current - previous - {entity_id})})
        return {"start_entity_id": entity_id, "k": depth, "levels": levels}

    def calculate_path_strength(self, source_id: str, target_id: str) -> dict:
        path = self.find_path(source_id, target_id)
        weights = [float(edge.get("weight", 1.0)) for edge in path["edges"]]
        strength = 0.0 if not path["found"] else round(prod(weights), 4)
        return {"source_id": source_id, "target_id": target_id, "strength": strength, "path": path}
