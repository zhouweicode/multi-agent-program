"""Milvus Lite read proxy isolated from the API model runtime."""
from __future__ import annotations

import json
import subprocess
import sys

from models.settings import Settings
from services.telemetry import traced_span


class MilvusSubprocessEntityRepository:
    """Run local Milvus reads outside the process that owns the LLM runtime."""

    backend = "milvus"

    def __init__(self, settings: Settings):
        self.settings = settings

    def _call(self, operation: str, **payload):
        request = {
            "operation": operation,
            "uri": self.settings.milvus_uri,
            "token": self.settings.milvus_token,
            "collection": self.settings.milvus_collection,
            "embedding_provider": self.settings.embedding_provider,
            "embedding_model_name": self.settings.embedding_model_name,
            "embedding_cache_dir": self.settings.embedding_cache_dir,
            "embedding_dimension": self.settings.embedding_dimension,
            "rrf_k": self.settings.milvus_rrf_k,
            **payload,
        }
        with traced_span(f"db.milvus.{operation}", "database", {
            "db.system": "milvus", "db.collection.name": self.settings.milvus_collection,
            "db.transport": "subprocess",
        }):
            completed = subprocess.run(
                [sys.executable, "-m", "scripts.query_milvus_entities"],
                input=json.dumps(request, ensure_ascii=False), text=True,
                capture_output=True, timeout=max(5.0, self.settings.model_request_timeout), check=False,
            )
        if completed.returncode != 0:
            detail = completed.stderr.strip().splitlines()
            raise RuntimeError(detail[-1] if detail else "Milvus worker failed")
        return json.loads(completed.stdout)

    def health(self) -> dict:
        return self._call("health")

    def search_scholars(self, mention: str, limit: int = 10) -> list[dict]:
        return self._call("search", mention=mention, limit=limit)

    def get_scholar(self, canonical_id: str) -> dict | None:
        return self._call("get", canonical_id=canonical_id)

    def close(self) -> None:
        return None
