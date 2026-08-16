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
            row["evidence_id"] = row["properties"].get("evidence_id") or f"neo4j_relation_{row['source']}_{row['relation']}_{row['target']}"
            row["source_name"] = "neo4j:relationship"
            row["source"] = row.pop("source")
            row["source_backend"] = "neo4j:relationship"
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
                    "evidence_id": properties.get("evidence_id") or
                                   f"neo4j_relation_{self._node_id(relationship.start_node)}_{relationship.type}_{self._node_id(relationship.end_node)}",
                    "source_backend": "neo4j:relationship",
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

    def get_person_company_roles(self, entity_ids: list[str]) -> list[dict]:
        """查询专家与企业间的真实图关系；关系类型作为企业角色保留。"""
        query = """
            MATCH (s:Scholar)-[r]-(c:Enterprise)
            WHERE s.scholar_id IN $entity_ids
            RETURN s.scholar_id AS entity_id, c.enterprise_id AS company_id,
                   coalesce(c.name_zh, c.name, c.name_en, c.enterprise_id) AS company_name,
                   coalesce(r.role, r.position, type(r)) AS role,
                   coalesce(r.start_year, r.year) AS start_year,
                   type(r) AS relation
            ORDER BY entity_id, company_id
        """
        rows = self._read(query, entity_ids=entity_ids)
        for row in rows:
            row.update({"evidence_id": f"neo4j_company_role_{row['entity_id']}_{row['company_id']}_{row['relation']}",
                        "source": "neo4j:Scholar-Enterprise"})
        return rows

    def get_company_projects(self, company_id: str) -> list[dict]:
        query = """
            MATCH (c:Enterprise)-[r]-(p:Project) WHERE c.enterprise_id = $company_id
            OPTIONAL MATCH (s:Scholar)-[]-(p)
            RETURN p.project_id AS project_id, c.enterprise_id AS company_id,
                   coalesce(c.name_zh, c.name, c.name_en, c.enterprise_id) AS company_name,
                   coalesce(p.title, p.name, p.project_id) AS name,
                   collect(DISTINCT s.scholar_id) AS participant_ids,
                   coalesce(p.start_year, p.year) AS start_year,
                   coalesce(p.end_year, p.start_year, p.year) AS end_year
            ORDER BY project_id
        """
        rows = self._read(query, company_id=company_id)
        for row in rows:
            row.update({"evidence_id": f"neo4j_company_project_{row['project_id']}",
                        "source": "neo4j:Enterprise-Project"})
        return rows

    def get_company_patents(self, company_id: str) -> list[dict]:
        query = """
            MATCH (c:Enterprise)-[r]-(p:Patent) WHERE c.enterprise_id = $company_id
            OPTIONAL MATCH (s:Scholar)-[]-(p)
            RETURN p.patent_id AS patent_id, c.enterprise_id AS company_id,
                   coalesce(c.name_zh, c.name, c.name_en, c.enterprise_id) AS company_name,
                   coalesce(p.title, p.name, p.publication_number, p.patent_id) AS title,
                   collect(DISTINCT s.scholar_id) AS inventor_ids
            ORDER BY patent_id
        """
        rows = self._read(query, company_id=company_id)
        for row in rows:
            row.update({"evidence_id": f"neo4j_company_patent_{row['patent_id']}",
                        "source": "neo4j:Enterprise-Patent"})
        return rows

    def get_chain_structure(self, chain_id: str) -> dict:
        query = """
            MATCH (root:IndustrySegment {segment_id: $chain_id})
            OPTIONAL MATCH (root)-[r]-(child:IndustrySegment)
            RETURN root.segment_id AS chain_id,
                   coalesce(root.name_zh, root.name, root.segment_id) AS name,
                   collect(DISTINCT {node_id: child.segment_id,
                       name: coalesce(child.name_zh, child.name, child.segment_id),
                       level: coalesce(child.level, type(r))}) AS node_details
        """
        rows = self._read(query, chain_id=chain_id)
        if not rows:
            return {"error": "CHAIN_NOT_FOUND", "chain_id": chain_id}
        row = rows[0]
        row["node_details"] = [item for item in row.get("node_details", []) if item.get("node_id")]
        row["nodes"] = [item["node_id"] for item in row["node_details"]]
        return row

    def get_node_companies(self, node_id: str) -> list[dict]:
        query = """
            MATCH (n:IndustrySegment {segment_id: $node_id})-[]-(c:Enterprise)
            RETURN DISTINCT c.enterprise_id AS company_id,
                   coalesce(c.name_zh, c.name, c.name_en, c.enterprise_id) AS name,
                   coalesce(c.industry, n.name_zh, n.name) AS industry
            ORDER BY company_id
        """
        return self._read(query, node_id=node_id)

    def get_node_events(self, node_id: str) -> list[dict]:
        query = """
            MATCH (n:IndustrySegment {segment_id: $node_id})-[]-(e:IndustryEvent)
            RETURN DISTINCT e.event_id AS event_id, n.segment_id AS node_id,
                   coalesce(e.title, e.name, e.event_id) AS title,
                   toString(coalesce(e.date, e.event_date, e.year)) AS date,
                   coalesce(e.importance, e.score, 0) AS importance
            ORDER BY importance DESC, event_id
        """
        rows = self._read(query, node_id=node_id)
        for row in rows:
            row.update({"evidence_id": f"neo4j_industry_event_{row['event_id']}",
                        "source": "neo4j:IndustrySegment-IndustryEvent"})
        return rows
