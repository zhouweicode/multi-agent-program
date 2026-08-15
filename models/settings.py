"""第四阶段模型与持久化配置，只从环境变量读取，不在业务代码写死密钥。"""
import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv


# 所有入口（FastAPI、demo、测试脚本）导入统一配置层时自动加载项目根目录 .env。
# override=False 保证终端、容器和部署平台显式注入的环境变量拥有更高优先级。
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env", override=False)


@dataclass(frozen=True)
class Settings:
    model_provider: str = "mock"
    model_name: str = "glm-5.2"
    model_api_key: str | None = None
    model_base_url: str | None = None
    model_temperature: float = 0
    model_request_timeout: float = 60
    model_max_retries: int = 1
    checkpoint_db_path: str = ".runtime/checkpoints.sqlite"
    entity_backend: str = "milvus"
    achievement_backend: str = "mock"
    graph_backend: str = "mock"
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_database: str = "gkx"
    mysql_user: str = "root"
    mysql_password: str | None = None
    neo4j_uri: str = "bolt://127.0.0.1:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str | None = None
    neo4j_database: str = "neo4j"
    embedding_provider: str = "bge_m3"
    embedding_model_name: str = "BAAI/bge-m3"
    embedding_cache_dir: str = ".runtime/huggingface"
    embedding_dimension: int = 1024
    milvus_uri: str = ".runtime/milvus-bge-m3.db"
    milvus_token: str | None = None
    milvus_collection: str = "scholar_entities"
    milvus_rrf_k: int = 60

    @classmethod
    def from_env(cls) -> "Settings":
        api_key = os.getenv("MODEL_API_KEY") or os.getenv("ZHIPUAI_API_KEY") or os.getenv("OPENAI_API_KEY")
        configured_provider = os.getenv("MODEL_PROVIDER", "auto").lower()
        provider = "openai" if configured_provider == "auto" and api_key else ("mock" if configured_provider == "auto" else configured_provider)
        return cls(model_provider=provider,
                   model_name=os.getenv("MODEL_NAME", "glm-5.2"),
                   model_api_key=api_key,
                   model_base_url=os.getenv("MODEL_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/"),
                   model_temperature=float(os.getenv("MODEL_TEMPERATURE", "0")),
                   model_request_timeout=float(os.getenv("MODEL_REQUEST_TIMEOUT", "60")),
                   model_max_retries=int(os.getenv("MODEL_MAX_RETRIES", "1")),
                   checkpoint_db_path=os.getenv("CHECKPOINT_DB_PATH", ".runtime/checkpoints.sqlite"),
                   entity_backend=os.getenv("ENTITY_BACKEND", "milvus").lower(),
                   achievement_backend=os.getenv("ACHIEVEMENT_BACKEND", "mock").lower(),
                   graph_backend=os.getenv("GRAPH_BACKEND", "mock").lower(),
                   mysql_host=os.getenv("MYSQL_HOST", "127.0.0.1"),
                   mysql_port=int(os.getenv("MYSQL_PORT", "3306")),
                   mysql_database=os.getenv("MYSQL_DATABASE", "gkx"),
                   mysql_user=os.getenv("MYSQL_USER", "root"),
                   mysql_password=os.getenv("MYSQL_PASSWORD"),
                   neo4j_uri=os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687"),
                   neo4j_user=os.getenv("NEO4J_USER", "neo4j"),
                   neo4j_password=os.getenv("NEO4J_PASSWORD"),
                   neo4j_database=os.getenv("NEO4J_DATABASE", "neo4j"),
                   embedding_provider=os.getenv("EMBEDDING_PROVIDER", "bge_m3").lower(),
                   embedding_model_name=os.getenv("EMBEDDING_MODEL_NAME", "BAAI/bge-m3"),
                   embedding_cache_dir=os.getenv("EMBEDDING_CACHE_DIR", ".runtime/huggingface"),
                   embedding_dimension=int(os.getenv("EMBEDDING_DIMENSION", "1024")),
                   milvus_uri=os.getenv("GRAPHRAG_MILVUS_URI", ".runtime/milvus-bge-m3.db"),
                   milvus_token=os.getenv("MILVUS_TOKEN"),
                   milvus_collection=os.getenv("MILVUS_COLLECTION", "scholar_entities"),
                   milvus_rrf_k=int(os.getenv("MILVUS_RRF_K", "60")))
