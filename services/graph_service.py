"""图服务：按环境配置选择 Mock 图或 Neo4j 仓储。"""
from collections import deque
from math import prod
from typing import Any

from data.mock_graph import GRAPH_EDGES
from models.graph_queries import (
    AggregateGraphInput,
    FilteredNeighborsInput,
    FindPathsInput,
    GraphFieldRef,
    GraphFilter,
    QuerySubgraphInput,
)
from models.settings import Settings
from repositories.entity_id_mapping_repository import EntityIdMappingRepository
from repositories.neo4j_repository import Neo4jGraphRepository
from services.graph_schema import (
    NODE_TYPES,
    graph_schema_payload,
    infer_mock_label,
    validate_field,
    validate_node_labels,
    validate_relation_types,
)


class GraphService:
    def __init__(self, repository=None):
        settings = Settings.from_env()
        self.backend = "mock"
        self.mapping = EntityIdMappingRepository()
        self.repository = repository
        self._owns_repository = repository is None
        if self.repository is None and settings.graph_backend == "neo4j":
            self.repository = Neo4jGraphRepository(settings)
            self.backend = "neo4j"
        elif self.repository is not None:
            self.backend = getattr(repository, "backend", "mock")

    def _graph_id(self, canonical_id: str) -> str:
        return self.mapping.to_backend(canonical_id, "neo4j") if self.backend == "neo4j" else canonical_id

    def _canonical_id(self, graph_id: str | None) -> str | None:
        return self.mapping.to_canonical(graph_id, "neo4j") if self.backend == "neo4j" and graph_id else graph_id

    def _canonical_path(self, path: dict) -> dict:
        if self.backend != "neo4j":
            return path
        path["nodes"] = [self._canonical_id(item) for item in path.get("nodes", [])]
        for edge in path.get("edges", []):
            edge["source"] = self._canonical_id(edge.get("source"))
            edge["target"] = self._canonical_id(edge.get("target"))
        return path

    def health(self) -> dict:
        if self.repository:
            return self.repository.health()
        return {"backend": "mock", "ready": True}

    def close(self) -> None:
        close = getattr(self.repository, "close", None) if self._owns_repository else None
        if close:
            close()

    @staticmethod
    def _neighbors(entity_id: str) -> list[tuple[str, dict]]:
        rows = []
        for edge in GRAPH_EDGES:
            if edge["source"] == entity_id:
                rows.append((edge["target"], edge))
            elif edge["target"] == entity_id:
                rows.append((edge["source"], edge))
        return rows

    def get_neighbors(self, entity_id: str) -> list[dict]:
        if self.repository:
            rows = self.repository.get_neighbors(self._graph_id(entity_id))
            if self.backend == "neo4j":
                for row in rows:
                    row["entity_id"] = self._canonical_id(row.get("entity_id"))
                    row["source"] = self._canonical_id(row.get("source"))
                    row["target"] = self._canonical_id(row.get("target"))
            return rows
        return [{"entity_id": other, **edge} for other, edge in self._neighbors(entity_id)]

    def find_path(self, source_id: str, target_id: str, max_hops: int = 4) -> dict:
        if self.repository:
            path = self.repository.find_path(self._graph_id(source_id), self._graph_id(target_id), max_hops)
            return self._canonical_path(path)
        queue = deque([(source_id, [source_id], [])])
        visited = {source_id}
        while queue:
            current, nodes, edges = queue.popleft()
            if current == target_id:
                return {"found": True, "nodes": nodes, "edges": edges, "hop_count": len(edges)}
            if len(edges) >= max_hops:
                continue
            for other, edge in self._neighbors(current):
                if other not in visited:
                    visited.add(other)
                    queue.append((other, nodes + [other], edges + [edge.copy()]))
        return {"found": False, "nodes": [], "edges": [], "hop_count": 0}

    def k_hop_expand(self, entity_id: str, k: int = 2) -> dict:
        if self.repository:
            result = self.repository.k_hop_expand(self._graph_id(entity_id), k)
            if self.backend == "neo4j":
                result["start_entity_id"] = entity_id
                for level in result.get("levels", []):
                    level["entity_ids"] = [self._canonical_id(item) for item in level["entity_ids"]]
            return result
        visited, frontier, levels = {entity_id}, {entity_id}, []
        for depth in range(1, k + 1):
            next_frontier = {other for node in frontier for other, _ in self._neighbors(node) if other not in visited}
            visited.update(next_frontier)
            levels.append({"hop": depth, "entity_ids": sorted(next_frontier)})
            frontier = next_frontier
            if not frontier:
                break
        return {"start_entity_id": entity_id, "k": k, "levels": levels}

    def calculate_path_strength(self, source_id: str, target_id: str) -> dict:
        if self.repository:
            result = self.repository.calculate_path_strength(self._graph_id(source_id), self._graph_id(target_id))
            if self.backend == "neo4j":
                result["source_id"], result["target_id"] = source_id, target_id
                result["path"] = self._canonical_path(result["path"])
            return result
        path = self.find_path(source_id, target_id)
        strength = 0.0 if not path["found"] else round(prod(edge["weight"] for edge in path["edges"]), 4)
        return {"source_id": source_id, "target_id": target_id, "strength": strength, "path": path}

    @staticmethod
    def _mock_node(entity_id: str) -> dict[str, Any]:
        label = infer_mock_label(entity_id)
        id_field = NODE_TYPES[label]["id_field"]
        return {
            "entity_id": entity_id,
            "labels": [label],
            "properties": {id_field: entity_id, "synthetic": True},
        }

    @staticmethod
    def _field_value(
        field: GraphFieldRef | GraphFilter,
        source: dict[str, Any],
        edge: dict[str, Any],
        target: dict[str, Any],
    ) -> Any:
        item = {"source": source, "relation": edge, "target": target}[field.scope]
        if field.scope == "relation":
            return item.get(field.field)
        return item.get("properties", {}).get(field.field)

    @classmethod
    def _matches_filter(
        cls,
        item: GraphFilter,
        source: dict[str, Any],
        edge: dict[str, Any],
        target: dict[str, Any],
    ) -> bool:
        actual = cls._field_value(item, source, edge, target)
        expected = item.value
        operations = {
            "eq": lambda: actual == expected,
            "ne": lambda: actual != expected,
            "gt": lambda: actual is not None and actual > expected,
            "gte": lambda: actual is not None and actual >= expected,
            "lt": lambda: actual is not None and actual < expected,
            "lte": lambda: actual is not None and actual <= expected,
            "in": lambda: actual in expected if isinstance(expected, list) else False,
            "contains": lambda: str(expected) in str(actual) if actual is not None else False,
        }
        return operations[item.operator]()

    @staticmethod
    def _edge_weight(edge: dict[str, Any]) -> float:
        return float(
            edge.get("weight", edge.get("confidence", edge.get("strength_score", 1.0)))
        )

    @classmethod
    def _mock_steps(cls, entity_id: str, direction: str):
        for edge in GRAPH_EDGES:
            if direction in {"out", "both"} and edge["source"] == entity_id:
                yield edge["target"], edge
            if direction in {"in", "both"} and edge["target"] == entity_id:
                yield edge["source"], edge

    @staticmethod
    def _validate_neighbor_query(query: FilteredNeighborsInput) -> None:
        validate_relation_types(query.relation_types)
        validate_node_labels(query.target_labels)
        for item in query.filters:
            if item.scope == "source":
                raise ValueError("get_neighbors_filtered 不支持未知源 Label 的 source 属性过滤")
            validate_field(item.scope, item.field, query.target_labels)

    def get_neighbors_filtered(self, query: FilteredNeighborsInput) -> list[dict]:
        self._validate_neighbor_query(query)
        if self.repository:
            mapped = query.model_copy(update={"entity_id": self._graph_id(query.entity_id)})
            rows = self.repository.get_neighbors_filtered(mapped)
            if self.backend == "neo4j":
                for row in rows:
                    row["entity_id"] = self._canonical_id(row.get("entity_id"))
                    row["source"] = self._canonical_id(row.get("source"))
                    row["target"] = self._canonical_id(row.get("target"))
            return rows
        rows = []
        for other, edge in self._mock_steps(query.entity_id, query.direction):
            source, target = self._mock_node(query.entity_id), self._mock_node(other)
            weight = self._edge_weight(edge)
            year = edge.get("year") or edge.get("start_year")
            if query.relation_types and edge["relation"] not in query.relation_types:
                continue
            if query.target_labels and target["labels"][0] not in query.target_labels:
                continue
            if query.start_year and (year is None or year < query.start_year):
                continue
            if query.end_year and (year is None or year > query.end_year):
                continue
            if query.min_weight is not None and weight < query.min_weight:
                continue
            if not all(self._matches_filter(item, source, edge, target) for item in query.filters):
                continue
            rows.append({
                "entity_id": other,
                "labels": target["labels"],
                "source": edge["source"],
                "target": edge["target"],
                "relation": edge["relation"],
                "weight": weight,
                "properties": {key: value for key, value in edge.items() if key not in {"source", "target", "relation"}},
                "evidence_id": edge.get("evidence_id"),
                "source_backend": "mock:graph",
            })
            if len(rows) >= query.limit:
                break
        return rows

    def find_paths(self, query: FindPathsInput) -> dict:
        validate_relation_types(query.relation_types)
        if self.repository:
            mapped = query.model_copy(update={
                "source_id": self._graph_id(query.source_id),
                "target_id": self._graph_id(query.target_id),
            })
            result = self.repository.find_paths(mapped)
            if self.backend == "neo4j":
                for path in result.get("paths", []):
                    self._canonical_path(path)
                result["source_id"], result["target_id"] = query.source_id, query.target_id
            return result
        found: list[dict[str, Any]] = []

        def walk(current: str, nodes: list[str], edges: list[dict]) -> None:
            if len(edges) > query.max_hops or len(found) >= 200:
                return
            if current == query.target_id and edges:
                score = round(prod(self._edge_weight(edge) for edge in edges), 6)
                found.append({"nodes": nodes, "edges": [edge.copy() for edge in edges],
                              "hop_count": len(edges), "score": score})
                return
            if len(edges) == query.max_hops:
                return
            for other, edge in self._mock_steps(current, query.direction):
                if other in nodes:
                    continue
                weight = self._edge_weight(edge)
                if query.relation_types and edge["relation"] not in query.relation_types:
                    continue
                if query.min_weight is not None and weight < query.min_weight:
                    continue
                walk(other, nodes + [other], edges + [edge])

        walk(query.source_id, [query.source_id], [])
        key = ((lambda row: (row["hop_count"], -row["score"]))
               if query.ranking == "shortest" else
               (lambda row: (-row["score"], row["hop_count"])))
        paths = sorted(found, key=key)[: query.top_k]
        return {"found": bool(paths), "source_id": query.source_id, "target_id": query.target_id,
                "path_count": len(paths), "ranking": query.ranking, "paths": paths}

    def query_subgraph(self, query: QuerySubgraphInput) -> dict:
        validate_node_labels(query.node_labels)
        validate_relation_types(query.relation_types)
        if self.repository:
            mapped = query.model_copy(update={
                "seed_entity_ids": [self._graph_id(item) for item in query.seed_entity_ids]
            })
            result = self.repository.query_subgraph(mapped)
            if self.backend == "neo4j":
                result["seed_entity_ids"] = query.seed_entity_ids
                for node in result.get("nodes", []):
                    node["entity_id"] = self._canonical_id(node.get("entity_id"))
                for edge in result.get("edges", []):
                    edge["source"] = self._canonical_id(edge.get("source"))
                    edge["target"] = self._canonical_id(edge.get("target"))
            return result
        nodes = {item: self._mock_node(item) for item in query.seed_entity_ids}
        frontier = set(query.seed_entity_ids)
        expanded = set(query.seed_entity_ids)
        edges: dict[tuple[str, str, str], dict[str, Any]] = {}
        truncated = False
        for _depth in range(query.max_hops):
            next_frontier = set()
            for current in sorted(frontier):
                for other, edge in self._mock_steps(current, query.direction):
                    label = infer_mock_label(other)
                    if query.node_labels and label not in query.node_labels:
                        continue
                    if query.relation_types and edge["relation"] not in query.relation_types:
                        continue
                    if other not in nodes and len(nodes) >= query.max_nodes:
                        truncated = True
                        continue
                    key = (edge["source"], edge["relation"], edge["target"])
                    if key not in edges and len(edges) >= query.max_edges:
                        truncated = True
                        continue
                    nodes.setdefault(other, self._mock_node(other))
                    edges[key] = edge.copy() | {"source_backend": "mock:graph"}
                    next_frontier.add(other)
            frontier = next_frontier - expanded
            expanded.update(next_frontier)
            if not frontier:
                break
        return {"seed_entity_ids": query.seed_entity_ids, "nodes": list(nodes.values()),
                "edges": list(edges.values()), "node_count": len(nodes), "edge_count": len(edges),
                "truncated": truncated}

    @staticmethod
    def _validate_aggregate_query(query: AggregateGraphInput) -> None:
        validate_node_labels([query.source_label])
        validate_node_labels([query.target_label] if query.target_label else [])
        validate_relation_types([query.relation_type] if query.relation_type else [])
        labels = {"source": [query.source_label], "target": [query.target_label] if query.target_label else []}
        for item in query.filters + query.group_by + [metric.field for metric in query.metrics if metric.field]:
            validate_field(item.scope, item.field, labels.get(item.scope, []))

    def aggregate_graph(self, query: AggregateGraphInput) -> dict:
        self._validate_aggregate_query(query)
        if self.repository:
            return self.repository.aggregate_graph(query)
        node_ids = {edge["source"] for edge in GRAPH_EDGES} | {edge["target"] for edge in GRAPH_EDGES}
        nodes = {item: self._mock_node(item) for item in node_ids}
        records = []
        if query.relation_type:
            for edge in GRAPH_EDGES:
                orientations = (
                    [(edge["source"], edge["target"])]
                    if query.direction == "out"
                    else [(edge["target"], edge["source"])]
                    if query.direction == "in"
                    else [
                        (edge["source"], edge["target"]),
                        (edge["target"], edge["source"]),
                    ]
                )
                for source_id, target_id in orientations:
                    source, target = nodes[source_id], nodes[target_id]
                    if source["labels"][0] != query.source_label or edge["relation"] != query.relation_type:
                        continue
                    if query.target_label and target["labels"][0] != query.target_label:
                        continue
                    if all(self._matches_filter(item, source, edge, target) for item in query.filters):
                        records.append((source, edge, target))
        else:
            records = [(node, {}, {}) for node in nodes.values() if node["labels"][0] == query.source_label]
            records = [row for row in records if all(self._matches_filter(item, *row) for item in query.filters)]

        groups: dict[tuple[Any, ...], list[tuple[dict, dict, dict]]] = {}
        for record in records:
            key = tuple(self._field_value(item, *record) for item in query.group_by)
            groups.setdefault(key, []).append(record)
        if not query.group_by and not groups:
            groups[()] = []
        rows = []
        for key, grouped in groups.items():
            row = {(field.alias or f"{field.scope}_{field.field}"): value
                   for field, value in zip(query.group_by, key, strict=True)}
            for metric in query.metrics:
                values = ([self._field_value(metric.field, *record) for record in grouped]
                          if metric.field else [1 for _ in grouped])
                non_null = [value for value in values if value is not None]
                if metric.operation == "count":
                    value = len(non_null)
                elif metric.operation == "count_distinct":
                    value = len(set(non_null))
                elif metric.operation == "sum":
                    value = sum(non_null) if non_null else 0
                elif metric.operation == "avg":
                    value = (sum(non_null) / len(non_null)) if non_null else None
                elif metric.operation == "min":
                    value = min(non_null) if non_null else None
                else:
                    value = max(non_null) if non_null else None
                row[metric.alias] = value
            rows.append(row)
        for order in reversed(query.order_by):
            rows.sort(key=lambda row, field=order.field: (row.get(field) is None, row.get(field)),
                      reverse=order.direction == "desc")
        return {"rows": rows[: query.limit], "row_count": min(len(rows), query.limit),
                "truncated": len(rows) > query.limit}

    def get_graph_schema(self) -> dict:
        return graph_schema_payload()
