"""Neo4j 只读图仓储，对外返回与原 Mock 图工具兼容的数据结构。"""
from __future__ import annotations

from math import prod
from typing import Any

from models.graph_queries import (
    AggregateGraphInput,
    FilteredNeighborsInput,
    FindPathsInput,
    GraphFilter,
    QuerySubgraphInput,
)
from models.settings import Settings
from services.graph_schema import (
    validate_field,
    validate_node_labels,
    validate_relation_types,
)
from services.telemetry import traced_span


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
        with traced_span("db.neo4j.read", "database", {
            "db.system": "neo4j", "db.namespace": self.settings.neo4j_database,
            "db.parameter_count": len(parameters),
        }), self.driver.session(database=self.settings.neo4j_database) as session:
            records = session.execute_read(lambda tx: list(tx.run(query, **parameters)))
        return [{key: _json_value(record[key]) for key in record} for record in records]

    def _managed(self, variable: str) -> str:
        return f" AND {variable}.synthetic = true" if self.settings.neo4j_managed_only else ""

    @staticmethod
    def _pattern(source: str, relation: str, target: str, direction: str) -> str:
        if direction == "out":
            return f"({source})-[{relation}]->({target})"
        if direction == "in":
            return f"({source})<-[{relation}]-({target})"
        return f"({source})-[{relation}]-({target})"

    @staticmethod
    def _compile_filters(
        filters: list[GraphFilter], parameters: dict[str, Any]
    ) -> list[str]:
        aliases = {"source": "s", "relation": "r", "target": "t"}
        operators = {
            "eq": "=",
            "ne": "<>",
            "gt": ">",
            "gte": ">=",
            "lt": "<",
            "lte": "<=",
        }
        clauses = []
        for index, item in enumerate(filters):
            parameter = f"filter_{index}"
            parameters[parameter] = item.value
            expression = f"{aliases[item.scope]}.`{item.field}`"
            if item.operator in operators:
                clauses.append(f"{expression} {operators[item.operator]} ${parameter}")
            elif item.operator == "in":
                if not isinstance(item.value, list):
                    raise ValueError("in 操作符的 value 必须是数组")
                clauses.append(f"{expression} IN ${parameter}")
            else:
                clauses.append(f"toString({expression}) CONTAINS toString(${parameter})")
        return clauses

    def _path_payload(self, path: Any, score: float | None = None) -> dict[str, Any]:
        nodes = [self._node_id(node) for node in path.nodes]
        edges = []
        for relationship in path.relationships:
            properties = dict(relationship)
            source = self._node_id(relationship.start_node)
            target = self._node_id(relationship.end_node)
            edges.append(
                {
                    "source": source,
                    "target": target,
                    "relation": relationship.type,
                    "weight": float(
                        properties.get(
                            "weight",
                            properties.get(
                                "confidence",
                                properties.get(
                                    "strength_score", properties.get("importance", 1.0)
                                ),
                            ),
                        )
                    ),
                    "properties": _json_value(properties),
                    "evidence_id": properties.get("evidence_id")
                    or f"neo4j_relation_{source}_{relationship.type}_{target}",
                    "source_backend": "neo4j:relationship",
                }
            )
        value = {
            "nodes": nodes,
            "edges": edges,
            "hop_count": len(edges),
        }
        if score is not None:
            value["score"] = round(float(score), 6)
        return value

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
            MATCH (n) WHERE {node_id} = $entity_id{self._managed('n')}
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
            MATCH (s) WHERE {source_expression} = $source_id{self._managed('s')}
            WITH s ORDER BY {_priority_expression('s')} LIMIT 1
            MATCH (t) WHERE {target_expression} = $target_id{self._managed('t')}
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

        with traced_span("db.neo4j.shortest_path", "database", {
            "db.system": "neo4j", "db.namespace": self.settings.neo4j_database,
        }), self.driver.session(database=self.settings.neo4j_database) as session:
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
                MATCH (s) WHERE {start_expression} = $entity_id{self._managed('s')}
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

    def get_neighbors_filtered(self, query: FilteredNeighborsInput) -> list[dict]:
        validate_relation_types(query.relation_types)
        validate_node_labels(query.target_labels)
        for item in query.filters:
            if item.scope == "source":
                raise ValueError(
                    "get_neighbors_filtered 不支持未知源 Label 的 source 属性过滤"
                )
            validate_field(item.scope, item.field, query.target_labels)
        parameters: dict[str, Any] = {
            "entity_id": query.entity_id,
            "relation_types": query.relation_types,
            "target_labels": query.target_labels,
            "limit": query.limit,
            "start_year": query.start_year,
            "end_year": query.end_year,
            "min_weight": query.min_weight,
        }
        filters = self._compile_filters(query.filters, parameters)
        source_id = _id_expression("s")
        target_id = _id_expression("t")
        pattern = self._pattern("s", "r", "t", query.direction)
        clauses = ["true"]
        if query.relation_types:
            clauses.append("type(r) IN $relation_types")
        if query.target_labels:
            clauses.append("any(label IN labels(t) WHERE label IN $target_labels)")
        year = "coalesce(r.year, r.start_year, t.year, t.start_year)"
        if query.start_year is not None:
            clauses.append(f"{year} >= $start_year")
        if query.end_year is not None:
            clauses.append(f"{year} <= $end_year")
        weight = "coalesce(r.weight, r.confidence, r.strength_score, r.importance, 1.0)"
        if query.min_weight is not None:
            clauses.append(f"{weight} >= $min_weight")
        clauses.extend(filters)
        query_text = f"""
            MATCH (s) WHERE {source_id} = $entity_id{self._managed('s')}
            WITH s ORDER BY {_priority_expression('s')} LIMIT 1
            MATCH {pattern}
            WHERE {' AND '.join(clauses)}{self._managed('t')}
            RETURN {target_id} AS entity_id, labels(t) AS labels,
                   type(r) AS relation, properties(r) AS properties,
                   {source_id} AS source, {target_id} AS target,
                   {weight} AS weight
            ORDER BY weight DESC, relation, entity_id
            LIMIT $limit
        """
        rows = self._read(query_text, **parameters)
        for row in rows:
            row["weight"] = float(row["weight"])
            row["evidence_id"] = row["properties"].get("evidence_id") or (
                f"neo4j_relation_{row['source']}_{row['relation']}_{row['target']}"
            )
            row["source_backend"] = "neo4j:relationship"
        return rows

    def find_paths(self, query: FindPathsInput) -> dict:
        validate_relation_types(query.relation_types)
        hops = max(1, min(query.max_hops, 6))
        path_pattern = self._pattern("s", f"*1..{hops}", "t", query.direction)
        source_id = _id_expression("s")
        target_id = _id_expression("t")
        conditions = [
            "all(node IN nodes(p) WHERE single(other IN nodes(p) WHERE other = node))"
        ]
        if query.relation_types:
            conditions.append(
                "all(rel IN relationships(p) WHERE type(rel) IN $relation_types)"
            )
        if query.min_weight is not None:
            conditions.append(
                "all(rel IN relationships(p) WHERE "
                "coalesce(rel.weight, rel.confidence, rel.strength_score, rel.importance, 1.0) >= $min_weight)"
            )
        if self.settings.neo4j_managed_only:
            conditions.append("all(node IN nodes(p) WHERE node.synthetic = true)")
        order = (
            "length(p) ASC, score DESC"
            if query.ranking == "shortest"
            else "score DESC, length(p) ASC"
        )
        query_text = f"""
            MATCH (s) WHERE {source_id} = $source_id{self._managed('s')}
            WITH s ORDER BY {_priority_expression('s')} LIMIT 1
            MATCH (t) WHERE {target_id} = $target_id{self._managed('t')}
            WITH s, t ORDER BY {_priority_expression('t')} LIMIT 1
            MATCH p = {path_pattern}
            WHERE {' AND '.join(conditions)}
            WITH p, reduce(score = 1.0, rel IN relationships(p) |
                 score * coalesce(rel.weight, rel.confidence, rel.strength_score, rel.importance, 1.0)) AS score
            RETURN p, score
            ORDER BY {order}
            LIMIT $top_k
        """

        def load_paths(tx):
            return [
                self._path_payload(record["p"], record["score"])
                for record in tx.run(
                    query_text,
                    source_id=query.source_id,
                    target_id=query.target_id,
                    relation_types=query.relation_types,
                    min_weight=query.min_weight,
                    top_k=query.top_k,
                )
            ]

        with traced_span(
            "db.neo4j.find_paths",
            "database",
            {"db.system": "neo4j", "db.namespace": self.settings.neo4j_database},
        ), self.driver.session(database=self.settings.neo4j_database) as session:
            paths = session.execute_read(load_paths)
        return {
            "found": bool(paths),
            "source_id": query.source_id,
            "target_id": query.target_id,
            "path_count": len(paths),
            "ranking": query.ranking,
            "paths": paths,
        }

    def query_subgraph(self, query: QuerySubgraphInput) -> dict:
        validate_node_labels(query.node_labels)
        validate_relation_types(query.relation_types)
        hops = max(1, min(query.max_hops, 3))
        pattern = self._pattern("s", f"*1..{hops}", "t", query.direction)
        start_id = _id_expression("s")
        conditions = []
        if query.relation_types:
            conditions.append(
                "all(rel IN relationships(p) WHERE type(rel) IN $relation_types)"
            )
        if query.node_labels:
            conditions.append(
                "all(node IN tail(nodes(p)) WHERE "
                "any(label IN labels(node) WHERE label IN $node_labels))"
            )
        if self.settings.neo4j_managed_only:
            conditions.append("all(node IN nodes(p) WHERE node.synthetic = true)")
        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        path_limit = min(1000, max(query.max_edges * 2, query.max_nodes))
        query_text = f"""
            MATCH (s) WHERE {start_id} IN $seed_entity_ids{self._managed('s')}
            MATCH p = {pattern}
            {where}
            RETURN p
            LIMIT $path_limit
        """

        def load_paths(tx):
            return [record["p"] for record in tx.run(
                query_text,
                seed_entity_ids=query.seed_entity_ids,
                relation_types=query.relation_types,
                node_labels=query.node_labels,
                path_limit=path_limit,
            )]

        with traced_span(
            "db.neo4j.query_subgraph",
            "database",
            {"db.system": "neo4j", "db.namespace": self.settings.neo4j_database},
        ), self.driver.session(database=self.settings.neo4j_database) as session:
            raw_paths = session.execute_read(load_paths)
        seed_query = f"""
            MATCH (s) WHERE {start_id} IN $seed_entity_ids{self._managed('s')}
            RETURN {start_id} AS entity_id, labels(s) AS labels,
                   properties(s) AS properties
        """
        seed_rows = self._read(seed_query, seed_entity_ids=query.seed_entity_ids)
        nodes: dict[str, dict[str, Any]] = {
            row["entity_id"]: row
            for row in seed_rows[: query.max_nodes]
            if row.get("entity_id")
        }
        edges: dict[tuple[str, str, str], dict[str, Any]] = {}
        truncated = len(raw_paths) >= path_limit or len(seed_rows) > query.max_nodes
        for path in raw_paths:
            payload = self._path_payload(path)
            for node in path.nodes:
                entity_id = self._node_id(node)
                if not entity_id or entity_id in nodes:
                    continue
                if len(nodes) >= query.max_nodes:
                    truncated = True
                    continue
                nodes[entity_id] = {
                    "entity_id": entity_id,
                    "labels": list(node.labels),
                    "properties": _json_value(dict(node)),
                }
            for edge in payload["edges"]:
                key = (edge["source"], edge["relation"], edge["target"])
                if key in edges:
                    continue
                if len(edges) >= query.max_edges:
                    truncated = True
                    continue
                if edge["source"] in nodes and edge["target"] in nodes:
                    edges[key] = edge
        return {
            "seed_entity_ids": query.seed_entity_ids,
            "nodes": list(nodes.values()),
            "edges": list(edges.values()),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "truncated": truncated,
        }

    def aggregate_graph(self, query: AggregateGraphInput) -> dict:
        validate_node_labels([query.source_label])
        validate_node_labels([query.target_label] if query.target_label else [])
        validate_relation_types([query.relation_type] if query.relation_type else [])
        labels = {
            "source": [query.source_label],
            "target": [query.target_label] if query.target_label else [],
        }
        fields = (
            query.filters
            + query.group_by
            + [metric.field for metric in query.metrics if metric.field]
        )
        for item in fields:
            validate_field(item.scope, item.field, labels.get(item.scope, []))
        aliases = {"source": "s", "relation": "r", "target": "t"}
        parameters: dict[str, Any] = {"limit": query.limit}
        clauses = self._compile_filters(query.filters, parameters)
        match = f"MATCH (s:`{query.source_label}`)"
        if query.relation_type:
            relation = f"r:`{query.relation_type}`"
            target = f"t:`{query.target_label}`" if query.target_label else "t"
            match += "\nMATCH " + self._pattern("s", relation, target, query.direction)
        if self.settings.neo4j_managed_only:
            clauses.append("s.synthetic = true")
            if query.relation_type:
                clauses.append("t.synthetic = true")
        where = "WHERE " + " AND ".join(clauses) if clauses else ""

        def expression(scope: str, field: str) -> str:
            return f"{aliases[scope]}.`{field}`"

        returns = []
        for item in query.group_by:
            alias = item.alias or f"{item.scope}_{item.field}"
            returns.append(f"{expression(item.scope, item.field)} AS `{alias}`")
        for metric in query.metrics:
            field = expression(metric.field.scope, metric.field.field) if metric.field else "*"
            if metric.operation == "count_distinct":
                aggregate = f"count(DISTINCT {field})"
            else:
                aggregate = f"{metric.operation}({field})"
            returns.append(f"{aggregate} AS `{metric.alias}`")
        orders = [f"`{item.field}` {item.direction.upper()}" for item in query.order_by]
        order_by = "ORDER BY " + ", ".join(orders) if orders else ""
        query_text = f"""
            {match}
            {where}
            RETURN {', '.join(returns)}
            {order_by}
            LIMIT $limit
        """
        rows = self._read(query_text, **parameters)
        return {"rows": rows, "row_count": len(rows), "truncated": len(rows) >= query.limit}

    def get_person_company_roles(self, entity_ids: list[str]) -> list[dict]:
        """查询专家与企业间的真实图关系；关系类型作为企业角色保留。"""
        query = """
            MATCH (s:Scholar)-[r]-(c:Enterprise)
            WHERE s.scholar_id IN $entity_ids%s%s
            RETURN s.scholar_id AS entity_id, c.enterprise_id AS company_id,
                   coalesce(c.name_zh, c.name, c.name_en, c.enterprise_id) AS company_name,
                   coalesce(r.role, properties(r)['position'], type(r)) AS role,
                   coalesce(r.start_year, r.year) AS start_year,
                   type(r) AS relation
            ORDER BY entity_id, company_id
        """
        query = query % (self._managed("s"), self._managed("c"))
        rows = self._read(query, entity_ids=entity_ids)
        for row in rows:
            row.update({"evidence_id": f"neo4j_company_role_{row['entity_id']}_{row['company_id']}_{row['relation']}",
                        "source": "neo4j:Scholar-Enterprise"})
        return rows

    def get_company_projects(self, company_id: str) -> list[dict]:
        query = """
            MATCH (c:Enterprise)-[r]-(p:Project) WHERE c.enterprise_id = $company_id%s%s
            OPTIONAL MATCH (s:Scholar)-[]-(p)
            RETURN p.project_id AS project_id, c.enterprise_id AS company_id,
                   coalesce(c.name_zh, c.name, c.name_en, c.enterprise_id) AS company_name,
                   coalesce(p.title, p.name, p.project_id) AS name,
                   collect(DISTINCT s.scholar_id) AS participant_ids,
                   coalesce(p.start_year, p.year) AS start_year,
                   coalesce(p.end_year, p.start_year, p.year) AS end_year
            ORDER BY project_id
        """
        query = query % (self._managed("c"), self._managed("p"))
        rows = self._read(query, company_id=company_id)
        for row in rows:
            row.update({"evidence_id": f"neo4j_company_project_{row['project_id']}",
                        "source": "neo4j:Enterprise-Project"})
        return rows

    def get_company_patents(self, company_id: str) -> list[dict]:
        query = """
            MATCH (c:Enterprise)-[r]-(p:Patent) WHERE c.enterprise_id = $company_id%s%s
            OPTIONAL MATCH (s:Scholar)-[]-(p)
            RETURN p.patent_id AS patent_id, c.enterprise_id AS company_id,
                   coalesce(c.name_zh, c.name, c.name_en, c.enterprise_id) AS company_name,
                   coalesce(p.title, p.name, p.publication_number, p.patent_id) AS title,
                   collect(DISTINCT s.scholar_id) AS inventor_ids
            ORDER BY patent_id
        """
        query = query % (self._managed("c"), self._managed("p"))
        rows = self._read(query, company_id=company_id)
        for row in rows:
            row.update({"evidence_id": f"neo4j_company_patent_{row['patent_id']}",
                        "source": "neo4j:Enterprise-Patent"})
        return rows

    def get_chain_structure(self, chain_id: str) -> dict:
        query = """
            MATCH (root:IndustrySegment {segment_id: $chain_id}) WHERE true%s
            OPTIONAL MATCH (root)-[r]-(child:IndustrySegment)
            RETURN root.segment_id AS chain_id,
                   coalesce(root.name_zh, root.name, root.segment_id) AS name,
                   collect(DISTINCT {node_id: child.segment_id,
                       name: coalesce(child.name_zh, child.name, child.segment_id),
                       level: coalesce(child.level, type(r))}) AS node_details
        """
        query = query % self._managed("root")
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
            WHERE true%s%s
            RETURN DISTINCT c.enterprise_id AS company_id,
                   coalesce(c.name_zh, c.name, c.name_en, c.enterprise_id) AS name,
                   coalesce(c.industry, n.name_zh, n.name) AS industry
            ORDER BY company_id
        """
        query = query % (self._managed("n"), self._managed("c"))
        return self._read(query, node_id=node_id)

    def get_node_events(self, node_id: str) -> list[dict]:
        query = """
            MATCH (n:IndustrySegment {segment_id: $node_id})-[]-(e:IndustryEvent)
            WHERE true%s%s
            RETURN DISTINCT e.event_id AS event_id, n.segment_id AS node_id,
                   coalesce(e.title, e.name, e.event_id) AS title,
                   toString(coalesce(properties(e)['date'], e.event_date, properties(e)['year'])) AS date,
                   coalesce(e.importance, properties(e)['score'], 0) AS importance
            ORDER BY importance DESC, event_id
        """
        query = query % (self._managed("n"), self._managed("e"))
        rows = self._read(query, node_id=node_id)
        for row in rows:
            row.update({"evidence_id": f"neo4j_industry_event_{row['event_id']}",
                        "source": "neo4j:IndustrySegment-IndustryEvent"})
        return rows

    def search_industry_segments(self, query_text: str, limit: int = 10) -> list[dict]:
        """Resolve a natural-language industry name before ID-based traversal."""
        managed = self._managed("n")
        query = f"""
            MATCH (n:IndustrySegment) WHERE true{managed}
              AND ($query_text = '' OR n.name_zh CONTAINS $query_text OR n.segment_id = $query_text)
            OPTIONAL MATCH (n)-[:HAS_EVENT]->(e:IndustryEvent)
            RETURN n.segment_id AS segment_id,
                   coalesce(n.name_zh, n.name, n.segment_id) AS name,
                   n.level AS level, n.parent_segment_id AS parent_segment_id,
                   count(e) AS event_count
            ORDER BY event_count DESC, segment_id
            LIMIT $limit
        """
        return self._read(query, query_text=query_text.strip(), limit=max(1, min(int(limit), 50)))
