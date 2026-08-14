"""图服务：按环境配置选择 Mock 图或 Neo4j 仓储。"""
from collections import deque
from math import prod

from data.mock_graph import GRAPH_EDGES
from models.settings import Settings
from repositories.neo4j_repository import Neo4jGraphRepository
from repositories.entity_id_mapping_repository import EntityIdMappingRepository


class GraphService:
    def __init__(self, repository=None):
        settings = Settings.from_env()
        self.backend = "mock"
        self.mapping = EntityIdMappingRepository()
        self.repository = repository
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
