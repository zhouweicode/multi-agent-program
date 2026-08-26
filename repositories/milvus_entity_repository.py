"""Milvus 实体仓储：Dense + Sparse Hybrid Search，并使用 RRF 融合。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from models.settings import Settings
from services.embedding_service import EmbeddingFactory, EmbeddingProvider
from services.telemetry import traced_span


class MilvusEntityRepository:
    backend = "milvus"

    def __init__(self, settings: Settings | None = None, embedding: EmbeddingProvider | None = None,
                 client: Any | None = None):
        # Injected clients are test/adaptor boundaries and must not inherit a developer's .env.
        self.settings = settings or (Settings() if client is not None else Settings.from_env())
        self.embedding = embedding or EmbeddingFactory.create(self.settings)
        self.collection = self.settings.milvus_collection
        if client is None:
            from pymilvus import MilvusClient
            if "://" not in self.settings.milvus_uri:
                Path(self.settings.milvus_uri).expanduser().parent.mkdir(parents=True, exist_ok=True)
            kwargs = {"uri": self.settings.milvus_uri}
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
        schema.add_field("canonical_id", DataType.VARCHAR, is_primary=True, max_length=128)
        schema.add_field("name", DataType.VARCHAR, max_length=256)
        schema.add_field("organization", DataType.VARCHAR, max_length=512)
        schema.add_field("title", DataType.VARCHAR, max_length=256)
        schema.add_field("search_text", DataType.VARCHAR, max_length=2048)
        schema.add_field("dense_vector", DataType.FLOAT_VECTOR, dim=self.embedding.dimension)
        schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)
        indexes = self.client.prepare_index_params()
        indexes.add_index("dense_vector", index_type="AUTOINDEX", metric_type="COSINE")
        indexes.add_index("sparse_vector", index_type="SPARSE_INVERTED_INDEX", metric_type="IP")
        self.client.create_collection(self.collection, schema=schema, index_params=indexes)
        self.client.load_collection(self.collection)

    @staticmethod
    def _text(row: dict) -> str:
        return " ".join(str(row.get(key, "")) for key in ("name", "organization", "title") if row.get(key))

    def count(self) -> int:
        with traced_span("db.milvus.count", "database", {
            "db.system": "milvus", "db.collection.name": self.collection,
        }):
            result = self.client.query(self.collection, filter="", output_fields=["count(*)"])
        return int(result[0]["count(*)"]) if result else 0

    def health(self) -> dict:
        return {"backend": "milvus", "ready": self.client.has_collection(self.collection),
                "collection": self.collection, "entity_count": self.count()}

    def upsert_entities(self, entities: list[dict]) -> int:
        if not entities:
            return 0
        texts = [self._text(row) for row in entities]
        dense_rows, sparse_rows = self.embedding.encode(texts)
        payload = []
        for row, text, dense, sparse in zip(entities, texts, dense_rows, sparse_rows):
            payload.append({
                "canonical_id": row.get("canonical_id") or row["entity_id"],
                "name": row.get("name", ""),
                "organization": row.get("organization", ""),
                "title": row.get("title", ""),
                "search_text": text,
                "dense_vector": dense,
                "sparse_vector": sparse,
            })
        result = self.client.upsert(self.collection, payload)
        return int(result.get("upsert_count", len(payload)))

    def delete_entities(self, canonical_ids: list[str]) -> int:
        if not canonical_ids:
            return 0
        escaped = [value.replace("\\", "\\\\").replace('"', '\\"') for value in canonical_ids]
        expression = "canonical_id in [" + ",".join(f'\"{value}\"' for value in escaped) + "]"
        self.client.delete(self.collection, filter=expression)
        return len(canonical_ids)

    def search_scholars(self, mention: str, limit: int = 10) -> list[dict]:
        from pymilvus import AnnSearchRequest, RRFRanker
        with traced_span("db.milvus.hybrid_search", "database", {
            "db.system": "milvus", "db.collection.name": self.collection, "db.limit": limit,
        }):
            dense, sparse = self.embedding.encode([mention])
            requests = [
                AnnSearchRequest([dense[0]], "dense_vector", {"metric_type": "COSINE"}, limit=limit),
                AnnSearchRequest([sparse[0]], "sparse_vector", {"metric_type": "IP"}, limit=limit),
            ]
            results = self.client.hybrid_search(
                self.collection, requests, RRFRanker(self.settings.milvus_rrf_k), limit=limit,
                output_fields=["canonical_id", "name", "organization", "title"],
            )
        rows = []
        for hit in results[0] if results else []:
            entity = hit.get("entity", {})
            rows.append({
                "entity_id": entity.get("canonical_id") or hit.get("id"),
                "canonical_id": entity.get("canonical_id") or hit.get("id"),
                "name": entity.get("name", ""),
                "organization": entity.get("organization", ""),
                "title": entity.get("title", ""),
                "retrieval_score": float(hit.get("distance", 0.0)),
                "retrieval_method": "dense+sparse+rrf",
            })
        exact = [row for row in rows if row["name"] == mention]
        return exact or rows

    def get_scholar(self, canonical_id: str) -> dict | None:
        safe_id = canonical_id.replace("\\", "\\\\").replace('"', '\\"')
        with traced_span("db.milvus.get", "database", {
            "db.system": "milvus", "db.collection.name": self.collection,
        }):
            rows = self.client.query(
                self.collection, filter=f'canonical_id == "{safe_id}"',
                output_fields=["canonical_id", "name", "organization", "title"], limit=1,
            )
        if not rows:
            return None
        row = rows[0]
        return {"entity_id": row["canonical_id"], "canonical_id": row["canonical_id"],
                "name": row["name"], "organization": row["organization"], "title": row["title"]}

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if close:
            close()
