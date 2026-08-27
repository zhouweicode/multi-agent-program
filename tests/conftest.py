"""测试环境始终使用轻量 Mock，避免收集测试时加载本地大模型或数据库。"""
import os

os.environ.setdefault("MODEL_PROVIDER", "mock")
os.environ.setdefault("ENTITY_BACKEND", "mock")
os.environ.setdefault("ACHIEVEMENT_BACKEND", "mock")
os.environ.setdefault("GRAPH_BACKEND", "mock")
os.environ.setdefault("ENTERPRISE_BACKEND", "mock")
os.environ.setdefault("INDUSTRY_BACKEND", "mock")
os.environ.setdefault("EMBEDDING_PROVIDER", "mock")
os.environ.setdefault("TOOL_TRANSPORT", "local")
os.environ.setdefault("WEB_SEARCH_PROVIDER", "disabled")
os.environ.setdefault("CHECKPOINT_DB_PATH", f"/tmp/multi-agent-program-tests-{os.getpid()}.sqlite")
os.environ.setdefault("RUN_REGISTRY_PATH", f"/tmp/multi-agent-program-runs-{os.getpid()}.sqlite")
os.environ.setdefault("OBSERVABILITY_DB_PATH", f"/tmp/multi-agent-program-observability-{os.getpid()}.sqlite")
os.environ.setdefault("CONVERSATION_MEMORY_DB_PATH", f"/tmp/multi-agent-program-memory-{os.getpid()}.sqlite")
os.environ.setdefault("QUERY_EXPERIENCE_DB_PATH", f"/tmp/multi-agent-program-experience-{os.getpid()}.sqlite")
