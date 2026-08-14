"""Embedding Provider：默认确定性离线向量，可切换真实 BGE-M3。"""
from __future__ import annotations

import hashlib
import math
from typing import Protocol

from models.settings import Settings


class EmbeddingProvider(Protocol):
    dimension: int

    def encode(self, texts: list[str]) -> tuple[list[list[float]], list[dict[int, float]]]: ...


class DeterministicHybridEmbedding:
    """无需下载模型的教学实现，同时生成 Dense/Sparse，保证测试可复现。"""
    def __init__(self, dimension: int = 128):
        self.dimension = dimension

    @staticmethod
    def _tokens(text: str) -> list[str]:
        compact = "".join(text.lower().split())
        chars = list(compact)
        return chars + [compact[index:index + 2] for index in range(max(0, len(compact) - 1))]

    def encode(self, texts: list[str]) -> tuple[list[list[float]], list[dict[int, float]]]:
        dense_rows, sparse_rows = [], []
        for text in texts:
            dense = [0.0] * self.dimension
            sparse: dict[int, float] = {}
            for token in self._tokens(text):
                digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
                index = int.from_bytes(digest, "big") % self.dimension
                sign = 1.0 if digest[0] % 2 == 0 else -1.0
                dense[index] += sign
                sparse[index] = sparse.get(index, 0.0) + 1.0
            norm = math.sqrt(sum(value * value for value in dense)) or 1.0
            dense_rows.append([value / norm for value in dense])
            sparse_rows.append(sparse)
        return dense_rows, sparse_rows


class BGEM3Embedding:
    """真实 BGE-M3 Provider；首次启用会由 FlagEmbedding 下载模型。"""
    def __init__(self, model_name: str):
        try:
            from FlagEmbedding import BGEM3FlagModel
        except ImportError as exc:
            raise RuntimeError("EMBEDDING_PROVIDER=bge_m3 需要安装可选依赖: pip install FlagEmbedding") from exc
        self.model = BGEM3FlagModel(model_name, use_fp16=False)
        probe = self.model.encode(["维度探测"], return_dense=True, return_sparse=True)
        self.dimension = len(probe["dense_vecs"][0])

    def encode(self, texts: list[str]) -> tuple[list[list[float]], list[dict[int, float]]]:
        result = self.model.encode(texts, return_dense=True, return_sparse=True)
        dense = [row.tolist() if hasattr(row, "tolist") else list(row) for row in result["dense_vecs"]]
        sparse = [{int(key): float(value) for key, value in row.items()} for row in result["lexical_weights"]]
        return dense, sparse


class EmbeddingFactory:
    @staticmethod
    def create(settings: Settings | None = None) -> EmbeddingProvider:
        settings = settings or Settings.from_env()
        if settings.embedding_provider == "mock":
            return DeterministicHybridEmbedding(settings.embedding_dimension)
        if settings.embedding_provider == "bge_m3":
            return BGEM3Embedding(settings.embedding_model_name)
        raise ValueError(f"不支持的 EMBEDDING_PROVIDER: {settings.embedding_provider}")
