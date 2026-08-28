"""Rebuildable Milvus hybrid index dedicated to user memory facts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from models.settings import Settings
from services.embedding_service import EmbeddingFactory, EmbeddingProvider
from services.telemetry import traced_span


def _escape_filter(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


class MilvusMemoryFactRepository:
    backend = "milvus"

    def __init__(self, settings: Settings | None = None,
                 embedding: EmbeddingProvider | None = None,
                 client: Any | None = None):
        self.settings = settings or (Settings() if client is not None else Settings.from_env())
        self.settings.validate_memory_settings()
        self.collection = self.settings.memory_milvus_collection
        self.embedding = embedding or EmbeddingFactory.create(self.settings)
        if client is None:
            from pymilvus import MilvusClient
            uri = self.settings.memory_milvus_uri
            if "://" not in uri:
                Path(uri).expanduser().parent.mkdir(parents=True, exist_ok=True)
            kwargs = {"uri": uri}
            if self.settings.milvus_token:
                kwargs["token"] = self.settings.milvus_token
            client = MilvusClient(**kwargs)
        self.client = client
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        if self.client.has_collection(self.collection):
            self.client.load_collection(self.collection)
            return
        from pymilvus import DataType
        schema = self.client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("fact_id", DataType.VARCHAR, is_primary=True, max_length=64)
        schema.add_field("user_id", DataType.VARCHAR, max_length=64)
        schema.add_field("agent_name", DataType.VARCHAR, max_length=64)
        schema.add_field("category", DataType.VARCHAR, max_length=64)
        schema.add_field("content", DataType.VARCHAR, max_length=4096)
        schema.add_field("dense_vector", DataType.FLOAT_VECTOR, dim=self.embedding.dimension)
        schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)
        indexes = self.client.prepare_index_params()
        indexes.add_index("dense_vector", index_type="AUTOINDEX", metric_type="COSINE")
        indexes.add_index("sparse_vector", index_type="SPARSE_INVERTED_INDEX", metric_type="IP")
        self.client.create_collection(self.collection, schema=schema, index_params=indexes)
        self.client.load_collection(self.collection)

    def upsert_facts(self, facts: list[dict[str, Any]]) -> int:
        if not facts:
            return 0
        texts = [str(fact.get("content") or "")[:4096] for fact in facts]
        dense_rows, sparse_rows = self.embedding.encode(texts)
        payload = [{
            "fact_id": fact["fact_id"],
            "user_id": fact["user_id"],
            "agent_name": fact.get("agent_name") or "",
            "category": fact.get("category") or "context",
            "content": text,
            "dense_vector": dense,
            "sparse_vector": sparse,
        } for fact, text, dense, sparse in zip(facts, texts, dense_rows, sparse_rows)]
        result = self.client.upsert(self.collection, payload)
        return int(result.get("upsert_count", len(payload)))

    def search(self, user_id: str, query: str, limit: int = 20,
               agent_name: str | None = None) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        from pymilvus import AnnSearchRequest, RRFRanker
        expression = (
            f'user_id == "{_escape_filter(user_id)}" and '
            f'agent_name == "{_escape_filter(agent_name or "")}"'
        )
        dense, sparse = self.embedding.encode([query])
        requests = [
            AnnSearchRequest([dense[0]], "dense_vector", {"metric_type": "COSINE"},
                             limit=limit, expr=expression),
            AnnSearchRequest([sparse[0]], "sparse_vector", {"metric_type": "IP"},
                             limit=limit, expr=expression),
        ]
        with traced_span("db.milvus.memory_search", "database", {
            "db.system": "milvus", "db.collection.name": self.collection,
            "db.limit": limit,
        }):
            results = self.client.hybrid_search(
                self.collection, requests,
                RRFRanker(self.settings.milvus_rrf_k), limit=limit,
                output_fields=["fact_id", "category"],
            )
        return [{
            "fact_id": hit.get("entity", {}).get("fact_id") or hit.get("id"),
            "category": hit.get("entity", {}).get("category"),
            "vector_score": float(hit.get("distance", 0)),
            "retrieval_method": "memory_dense+sparse+rrf",
        } for hit in (results[0] if results else [])]

    def delete_facts(self, fact_ids: list[str]) -> int:
        if not fact_ids:
            return 0
        values = ",".join(f'"{_escape_filter(fact_id)}"' for fact_id in fact_ids)
        self.client.delete(self.collection, filter=f"fact_id in [{values}]")
        return len(fact_ids)

    def delete_user_facts(self, user_id: str,
                          agent_name: str | None = None) -> int:
        expression = f'user_id == "{_escape_filter(user_id)}"'
        if agent_name is not None:
            expression += f' and agent_name == "{_escape_filter(agent_name)}"'
        self.client.delete(self.collection, filter=expression)
        return 1

    def health(self) -> dict[str, Any]:
        return {"backend": "milvus", "ready": self.client.has_collection(self.collection),
                "collection": self.collection,
                "isolated_from_entity_collection": self.collection != self.settings.milvus_collection}

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if close:
            close()
