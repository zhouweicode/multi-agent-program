"""Single-purpose Milvus read worker for the local online-query adapter."""
from __future__ import annotations

from dataclasses import replace
import json
import sys

from models.settings import Settings
from repositories.milvus_entity_repository import MilvusEntityRepository


def main() -> None:
    request = json.load(sys.stdin)
    settings = replace(
        Settings(), milvus_uri=request["uri"], milvus_token=request.get("token"),
        milvus_collection=request["collection"],
        embedding_provider=request.get("embedding_provider", "mock"),
        embedding_model_name=request.get("embedding_model_name", "BAAI/bge-m3"),
        embedding_cache_dir=request.get("embedding_cache_dir", ".runtime/huggingface"),
        embedding_dimension=int(request.get("embedding_dimension", 1024)),
        milvus_rrf_k=int(request.get("rrf_k", 60)),
    )
    repository = MilvusEntityRepository(settings)
    try:
        operation = request["operation"]
        if operation == "health":
            result = repository.health()
        elif operation == "search":
            result = repository.search_scholars(request["mention"], int(request.get("limit", 10)))
        elif operation == "get":
            result = repository.get_scholar(request["canonical_id"])
        else:
            raise ValueError(f"unsupported operation: {operation}")
        print(json.dumps(result, ensure_ascii=False))
    finally:
        repository.close()


if __name__ == "__main__":
    main()
