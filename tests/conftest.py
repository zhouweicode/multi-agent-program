"""测试环境始终使用轻量 Mock，避免收集测试时加载本地大模型或数据库。"""
import os

os.environ.setdefault("MODEL_PROVIDER", "mock")
os.environ.setdefault("ENTITY_BACKEND", "mock")
os.environ.setdefault("ACHIEVEMENT_BACKEND", "mock")
os.environ.setdefault("GRAPH_BACKEND", "mock")
os.environ.setdefault("EMBEDDING_PROVIDER", "mock")
os.environ.setdefault("CHECKPOINT_DB_PATH", f"/tmp/multi-agent-program-tests-{os.getpid()}.sqlite")
