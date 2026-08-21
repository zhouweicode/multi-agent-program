"""实体检索服务：权威库精确召回 + Milvus 语义补召回与确定性融合。"""
from __future__ import annotations

from data.mock_entities import MOCK_ENTITIES
from models.settings import Settings
from repositories.mysql_repository import MySQLRepository
from repositories.entity_id_mapping_repository import EntityIdMappingRepository
from repositories.milvus_entity_repository import MilvusEntityRepository


class EntityService:
    def __init__(self, repository=None, exact_repository=None, vector_repository=None,
                 backend: str | None = None, settings: Settings | None = None):
        settings = settings or Settings.from_env()
        configured_backend = backend or settings.entity_backend
        self.backend = "mock"
        self.settings = settings
        self.mapping = EntityIdMappingRepository()
        self.repository = repository
        self.exact_repository = exact_repository
        self.vector_repository = vector_repository
        if self.repository is None and configured_backend == "mysql":
            self.repository = MySQLRepository(settings)
            self.backend = "mysql"
        elif self.repository is None and configured_backend == "milvus":
            self.repository = MilvusEntityRepository(settings)
            self.backend = "milvus"
        elif self.repository is None and configured_backend == "hybrid":
            self.exact_repository = self.exact_repository or MySQLRepository(settings)
            self.vector_repository = self.vector_repository or MilvusEntityRepository(settings)
            self.backend = "hybrid"
        elif self.repository is not None:
            self.backend = getattr(repository, "backend", "mock")

    @staticmethod
    def _context_score(candidate: dict, context: str) -> tuple[float, list[str]]:
        """使用可解释的机构/职称上下文信号，不让向量分数覆盖精确身份事实。"""
        score, reasons = 0.0, []
        organization = str(candidate.get("organization", "")).strip()
        title = str(candidate.get("title", "")).strip()
        if organization and organization in context:
            score += 0.20
            reasons.append("问题上下文命中机构")
        if title and title in context:
            score += 0.05
            reasons.append("问题上下文命中职称")
        return score, reasons

    def _normalize_exact(self, rows: list[dict]) -> list[dict]:
        return [self.mapping.normalize_candidate(row, "mysql") for row in rows]

    def _search_repository(self, repository, mention: str) -> list[dict]:
        """兼容第一阶段只接收 mention 的教学 Repository。"""
        try:
            return repository.search_scholars(mention, limit=self.settings.entity_candidate_top_k)
        except TypeError as exc:
            if "limit" not in str(exc):
                raise
            return repository.search_scholars(mention)

    def _hybrid_search(self, mention: str, context: str) -> list[dict]:
        limit = self.settings.entity_candidate_top_k
        exact_rows = self._normalize_exact(self._search_repository(self.exact_repository, mention))
        query_text = " ".join(part for part in (mention, context) if part).strip()
        vector_rows = self.vector_repository.search_scholars(query_text, limit=limit)
        merged: dict[str, dict] = {}
        for rank, row in enumerate(vector_rows, 1):
            candidate = row.copy()
            exact_name = candidate.get("name") == mention
            if not exact_name and float(candidate.get("retrieval_score", 0.0)) < self.settings.entity_vector_min_score:
                continue
            candidate["vector_rank"] = rank
            candidate["vector_score"] = round(1.0 / rank, 4)
            candidate["exact_match"] = exact_name
            candidate["mysql_exact"] = False
            candidate["match_reasons"] = ["Milvus Dense+Sparse+RRF 召回"]
            merged[candidate["entity_id"]] = candidate
        for row in exact_rows:
            candidate = merged.get(row["entity_id"], {}) | row
            candidate["exact_match"] = True
            candidate["mysql_exact"] = True
            candidate.setdefault("match_reasons", []).append("MySQL 姓名精确匹配")
            merged[row["entity_id"]] = candidate
        candidates = []
        for candidate in merged.values():
            exact_score = 0.70 if candidate.get("exact_match") else 0.0
            context_score, reasons = self._context_score(candidate, context)
            vector_score = min(float(candidate.get("vector_score", 0.0)), 1.0) * 0.10
            candidate["context_score"] = round(context_score, 4)
            candidate["final_score"] = round(min(exact_score + context_score + vector_score, 1.0), 4)
            candidate["retrieval_method"] = "mysql+milvus+rrf" if candidate.get("mysql_exact") and candidate.get("vector_rank") else (
                "mysql_exact" if candidate.get("mysql_exact") else "milvus_dense+sparse+rrf")
            candidate["match_reasons"] = list(dict.fromkeys(candidate.get("match_reasons", []) + reasons))
            candidates.append(candidate)
        return sorted(candidates, key=lambda row: (-row["final_score"], row.get("entity_id", "")))[:limit]

    def search(self, mention: str, context: str = "") -> list[dict]:
        if self.backend == "hybrid":
            return self._hybrid_search(mention, context)
        if self.repository:
            rows = self._search_repository(self.repository, mention)
            normalized = ([self.mapping.normalize_candidate(row, "mysql") for row in rows]
                          if self.backend == "mysql" else [row.copy() for row in rows])
            for row in normalized:
                row["exact_match"] = row.get("name") == mention
            return normalized
        return [item.copy() | {"exact_match": True, "retrieval_method": "mock_exact"}
                for item in MOCK_ENTITIES if item["name"] == mention]

    def auto_resolve(self, candidates: list[dict]) -> str | None:
        """唯一精确候选直接确认；多候选必须同时满足绝对阈值与 Top1 分差。"""
        if not candidates:
            return None
        exact = [row for row in candidates if row.get("exact_match")]
        mysql_exact = [row for row in exact if row.get("mysql_exact") or "mysql" in row.get("retrieval_method", "")]
        if len(mysql_exact) == 1:
            return mysql_exact[0]["entity_id"]
        if len(candidates) == 1 and len(exact) == 1:
            return exact[0]["entity_id"]
        top = candidates[0]
        second_score = float(candidates[1].get("final_score", 0.0)) if len(candidates) > 1 else 0.0
        if (float(top.get("final_score", 0.0)) >= self.settings.entity_auto_resolve_threshold and
                float(top.get("final_score", 0.0)) - second_score >= self.settings.entity_score_gap_threshold):
            return top["entity_id"]
        return None

    def exists(self, entity_id: str) -> bool:
        if self.backend == "hybrid":
            backend_id = self.mapping.to_backend(entity_id, "mysql")
            return self.exact_repository.get_scholar(backend_id) is not None or self.vector_repository.get_scholar(entity_id) is not None
        if self.repository:
            backend_id = self.mapping.to_backend(entity_id, self.backend)
            return self.repository.get_scholar(backend_id) is not None
        return any(item["entity_id"] == entity_id for item in MOCK_ENTITIES)

    def health(self) -> dict:
        if self.backend == "hybrid":
            return {"backend": "hybrid", "ready": True,
                    "mysql": self.exact_repository.health(), "milvus": self.vector_repository.health()}
        health = getattr(self.repository, "health", None)
        return health() if health else {"backend": self.backend, "ready": True}

    def get(self, entity_id: str) -> dict | None:
        if self.backend == "hybrid":
            backend_id = self.mapping.to_backend(entity_id, "mysql")
            row = self.exact_repository.get_scholar(backend_id)
            return self.mapping.normalize_candidate(row, "mysql") if row else self.vector_repository.get_scholar(entity_id)
        if self.repository:
            backend_id = self.mapping.to_backend(entity_id, self.backend)
            row = self.repository.get_scholar(backend_id)
            return self.mapping.normalize_candidate(row, self.backend) if row and self.backend == "mysql" else row
        return next((x.copy() for x in MOCK_ENTITIES if x["entity_id"] == entity_id), None)

    def get_employment_history(self, entity_id: str) -> list[dict]:
        repository = self.exact_repository if self.backend == "hybrid" else self.repository
        if repository and hasattr(repository, "get_employment_history"):
            backend_id = self.mapping.to_backend(entity_id, "mysql" if self.backend == "hybrid" else self.backend)
            rows = repository.get_employment_history(backend_id)
            return [row | {"entity_id": entity_id} for row in rows]
        rows = {
            "person_zw_001": [{"organization": "清华大学", "role": "教授", "start_year": 2017, "end_year": None, "evidence_id": "ev_employment_zw_001"}],
            "person_lm_001": [{"organization": "清华大学", "role": "副教授", "start_year": 2019, "end_year": None, "evidence_id": "ev_employment_lm_001"}],
            "person_zw_002": [{"organization": "北京理工大学", "role": "研究员", "start_year": 2018, "end_year": None, "evidence_id": "ev_employment_zw_002"}],
            "person_lm_002": [{"organization": "中科院自动化所", "role": "研究员", "start_year": 2016, "end_year": None, "evidence_id": "ev_employment_lm_002"}],
        }
        return [{"entity_id": entity_id, **row} for row in rows.get(entity_id, [])]

    def get_education_history(self, entity_id: str) -> list[dict]:
        repository = self.exact_repository if self.backend == "hybrid" else self.repository
        if repository and hasattr(repository, "get_education_history"):
            backend_id = self.mapping.to_backend(entity_id, "mysql" if self.backend == "hybrid" else self.backend)
            rows = repository.get_education_history(backend_id)
            return [row | {"entity_id": entity_id} for row in rows]
        rows = {
            "person_zw_001": [{"institution": "清华大学", "degree": "博士", "start_year": 2008, "end_year": 2013,
                               "evidence_id": "ev_education_zw_001"}],
            "person_lm_001": [{"institution": "清华大学", "degree": "博士", "start_year": 2010, "end_year": 2015,
                               "evidence_id": "ev_education_lm_001"}],
        }
        return [{"entity_id": entity_id, **row} for row in rows.get(entity_id, [])]

    def close(self) -> None:
        for repository in (self.repository, self.exact_repository, self.vector_repository):
            close = getattr(repository, "close", None)
            if close:
                close()
