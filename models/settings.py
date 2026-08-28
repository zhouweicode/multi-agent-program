"""第四阶段模型与持久化配置，只从环境变量读取，不在业务代码写死密钥。"""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from mcp_runtime.config import MCPServerConfig, parse_mcp_servers, parse_transport_overrides

# 所有入口（FastAPI、demo、测试脚本）导入统一配置层时自动加载项目根目录 .env。
# override=False 保证终端、容器和部署平台显式注入的环境变量拥有更高优先级。
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env", override=False)


@dataclass(frozen=True)
class AgentModelConfig:
    """单个 Agent/Node 最终生效的模型配置。"""

    provider: str
    name: str
    api_key: str | None
    base_url: str | None
    temperature: float
    request_timeout: float
    max_retries: int
    input_cost_per_million: float
    output_cost_per_million: float
    cost_currency: str


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
    auth_db_path: str = ".runtime/users.sqlite"
    auth_required: bool = True
    auth_session_ttl_seconds: int = 86400
    auth_cookie_secure: bool = False
    auth_admin_password: str = "Admin@123"
    auth_researcher_password: str = "Research@123"
    auth_analyst_password: str = "Analyst@123"
    entity_backend: str = "milvus"
    achievement_backend: str = "mock"
    graph_backend: str = "mock"
    enterprise_backend: str = "mock"
    industry_backend: str = "mock"
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_database: str = "gkx"
    mysql_user: str = "root"
    mysql_password: str | None = None
    neo4j_uri: str = "bolt://127.0.0.1:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str | None = None
    neo4j_database: str = "neo4j"
    neo4j_managed_only: bool = False
    embedding_provider: str = "bge_m3"
    embedding_model_name: str = "BAAI/bge-m3"
    embedding_cache_dir: str = ".runtime/huggingface"
    embedding_dimension: int = 1024
    milvus_uri: str = ".runtime/milvus-bge-m3.db"
    milvus_token: str | None = None
    milvus_collection: str = "scholar_entities"
    milvus_rrf_k: int = 60
    entity_candidate_top_k: int = 10
    entity_auto_resolve_threshold: float = 0.90
    entity_score_gap_threshold: float = 0.15
    entity_vector_min_score: float = 0.02
    run_max_workers: int = 4
    run_timeout_seconds: float = 300
    run_registry_path: str = ".runtime/runs.sqlite"
    kg_workflow_registry_path: str = ".runtime/kg-workflow.sqlite"
    tool_transport: str = "local"
    mcp_server_url: str = "http://127.0.0.1:8100/mcp"
    mcp_request_timeout: float = 30
    mcp_server_host: str = "127.0.0.1"
    mcp_server_port: int = 8100
    mcp_server_path: str = "/mcp"
    mcp_servers: tuple[MCPServerConfig, ...] = ()
    tool_transport_overrides: tuple[tuple[str, str], ...] = ()
    web_search_provider: str = "disabled"
    web_search_api_key: str | None = None
    web_search_endpoint: str | None = None
    web_search_timeout: float = 15
    web_search_max_results: int = 5
    observability_db_path: str = ".runtime/observability.sqlite"
    memory_backend: str = "sqlite"
    memory_mysql_database: str = "gkx_runtime"
    conversation_memory_db_path: str = ".runtime/conversation-memory.sqlite"
    query_experience_db_path: str = ".runtime/query-experience.sqlite"
    long_term_memory_db_path: str = ".runtime/long-term-memory.sqlite"
    memory_extraction_enabled: bool = True
    memory_worker_poll_seconds: float = 1.0
    memory_worker_batch_size: int = 10
    memory_worker_lease_seconds: int = 60
    memory_worker_max_attempts: int = 3
    memory_fact_confidence_threshold: float = 0.85
    memory_fact_max_per_scope: int = 100
    memory_fact_review_days: int = 90
    memory_fact_similarity_threshold: float = 0.86
    memory_recall_top_k: int = 5
    memory_recall_candidate_limit: int = 100
    memory_recall_token_budget: int = 1000
    memory_milvus_uri: str = ".runtime/user-memory-milvus.db"
    memory_milvus_collection: str = "user_memory_facts_v1"
    memory_retrieval_backend: str = "mysql"
    query_experience_mode: str = "shadow"
    query_experience_scope_id: str = "local"
    query_experience_candidate_limit: int = 5
    query_experience_min_samples: int = 5
    query_experience_min_similarity: float = 0.72
    query_experience_min_confidence: float = 0.75
    workflow_version: str = "stage12-runtime-skills"
    prompt_version: str = "prompt-v1"
    model_input_cost_per_million: float = 0
    model_output_cost_per_million: float = 0
    model_cost_currency: str = "USD"

    def tool_transport_for(self, domain: str) -> str:
        overrides = dict(self.tool_transport_overrides)
        if domain in overrides:
            transport = overrides[domain]
        elif domain == "web" and any(
            item.enabled and (not item.domains or "web" in item.domains)
            for item in self.mcp_servers
        ):
            # 显式配置外部 Web MCP 后优先试用；override=local 可精确关闭。
            transport = "mcp"
        else:
            transport = self.tool_transport
        if transport not in {"local", "mcp"}:
            raise ValueError(f"领域 {domain} 的 Tool 传输只能是 local 或 mcp")
        return transport

    def resolved_mcp_servers(self) -> tuple[MCPServerConfig, ...]:
        if self.mcp_servers:
            return self.mcp_servers
        return (
            MCPServerConfig(
                name="default",
                target=self.mcp_server_url,
                domains=(
                    "talent",
                    "achievement",
                    "enterprise",
                    "industry",
                    "graph",
                    "verification",
                    "web",
                ),
            ),
        )

    def model_config(self, agent_name: str | None = None) -> AgentModelConfig:
        """解析每 Agent 模型配置；未配置的字段回退到全局 MODEL_*。

        例如 `ACHIEVEMENT_AGENT_MODEL_NAME`、`VERIFICATION_AGENT_MODEL_PROVIDER`。
        """
        prefix = ("".join(character if character.isalnum() else "_"
                          for character in agent_name.upper()) + "_") if agent_name else ""

        def env(name: str, default):
            return os.getenv(f"{prefix}MODEL_{name}", default) if prefix else default

        provider = str(env("PROVIDER", self.model_provider)).lower()
        api_key = env("API_KEY", self.model_api_key)
        if provider == "auto":
            provider = "openai" if api_key else "mock"
        return AgentModelConfig(
            provider=provider,
            name=str(env("NAME", self.model_name)),
            api_key=api_key,
            base_url=env("BASE_URL", self.model_base_url),
            temperature=float(env("TEMPERATURE", self.model_temperature)),
            request_timeout=float(env("REQUEST_TIMEOUT", self.model_request_timeout)),
            max_retries=int(env("MAX_RETRIES", self.model_max_retries)),
            input_cost_per_million=float(env("INPUT_COST_PER_MILLION", self.model_input_cost_per_million)),
            output_cost_per_million=float(env("OUTPUT_COST_PER_MILLION", self.model_output_cost_per_million)),
            cost_currency=str(env("COST_CURRENCY", self.model_cost_currency)),
        )

    def validate_memory_settings(self) -> None:
        if self.memory_milvus_collection == self.milvus_collection:
            raise ValueError(
                "MEMORY_MILVUS_COLLECTION 禁止与 MILVUS_COLLECTION 共用"
            )
        if self.memory_retrieval_backend not in {"mysql", "hybrid", "milvus"}:
            raise ValueError(
                "MEMORY_RETRIEVAL_BACKEND 仅支持 mysql、hybrid 或 milvus"
            )

    @classmethod
    def from_env(cls) -> "Settings":
        api_key = os.getenv("MODEL_API_KEY") or os.getenv("ZHIPUAI_API_KEY") or os.getenv("OPENAI_API_KEY")
        configured_provider = os.getenv("MODEL_PROVIDER", "auto").lower()
        provider = "openai" if configured_provider == "auto" and api_key else ("mock" if configured_provider == "auto" else configured_provider)
        settings = cls(model_provider=provider,
                   model_name=os.getenv("MODEL_NAME", "glm-5.2"),
                   model_api_key=api_key,
                   model_base_url=os.getenv("MODEL_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/"),
                   model_temperature=float(os.getenv("MODEL_TEMPERATURE", "0")),
                   model_request_timeout=float(os.getenv("MODEL_REQUEST_TIMEOUT", "60")),
                   model_max_retries=int(os.getenv("MODEL_MAX_RETRIES", "1")),
                   checkpoint_db_path=os.getenv("CHECKPOINT_DB_PATH", ".runtime/checkpoints.sqlite"),
                   auth_db_path=os.getenv("AUTH_DB_PATH", ".runtime/users.sqlite"),
                   auth_required=os.getenv("AUTH_REQUIRED", "true").lower() == "true",
                   auth_session_ttl_seconds=max(300, int(os.getenv("AUTH_SESSION_TTL_SECONDS", "86400"))),
                   auth_cookie_secure=os.getenv("AUTH_COOKIE_SECURE", "false").lower() == "true",
                   auth_admin_password=os.getenv("AUTH_ADMIN_PASSWORD", "Admin@123"),
                   auth_researcher_password=os.getenv("AUTH_RESEARCHER_PASSWORD", "Research@123"),
                   auth_analyst_password=os.getenv("AUTH_ANALYST_PASSWORD", "Analyst@123"),
                   entity_backend=os.getenv("ENTITY_BACKEND", "milvus").lower(),
                   achievement_backend=os.getenv("ACHIEVEMENT_BACKEND", "mock").lower(),
                   graph_backend=os.getenv("GRAPH_BACKEND", "mock").lower(),
                   enterprise_backend=os.getenv("ENTERPRISE_BACKEND", "mock").lower(),
                   industry_backend=os.getenv("INDUSTRY_BACKEND", "mock").lower(),
                   mysql_host=os.getenv("MYSQL_HOST", "127.0.0.1"),
                   mysql_port=int(os.getenv("MYSQL_PORT", "3306")),
                   mysql_database=os.getenv("MYSQL_DATABASE", "gkx"),
                   mysql_user=os.getenv("MYSQL_USER", "root"),
                   mysql_password=os.getenv("MYSQL_PASSWORD"),
                   neo4j_uri=os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687"),
                   neo4j_user=os.getenv("NEO4J_USER", "neo4j"),
                   neo4j_password=os.getenv("NEO4J_PASSWORD"),
                   neo4j_database=os.getenv("NEO4J_DATABASE", "neo4j"),
                   neo4j_managed_only=os.getenv("NEO4J_MANAGED_ONLY", "false").lower() == "true",
                   embedding_provider=os.getenv("EMBEDDING_PROVIDER", "bge_m3").lower(),
                   embedding_model_name=os.getenv("EMBEDDING_MODEL_NAME", "BAAI/bge-m3"),
                   embedding_cache_dir=os.getenv("EMBEDDING_CACHE_DIR", ".runtime/huggingface"),
                   embedding_dimension=int(os.getenv("EMBEDDING_DIMENSION", "1024")),
                   milvus_uri=os.getenv("GRAPHRAG_MILVUS_URI", ".runtime/milvus-bge-m3.db"),
                   milvus_token=os.getenv("MILVUS_TOKEN"),
                   milvus_collection=os.getenv("MILVUS_COLLECTION", "scholar_entities"),
                   milvus_rrf_k=int(os.getenv("MILVUS_RRF_K", "60")),
                   entity_candidate_top_k=int(os.getenv("ENTITY_CANDIDATE_TOP_K", "10")),
                   entity_auto_resolve_threshold=float(os.getenv("ENTITY_AUTO_RESOLVE_THRESHOLD", "0.90")),
                   entity_score_gap_threshold=float(os.getenv("ENTITY_SCORE_GAP_THRESHOLD", "0.15")),
                   entity_vector_min_score=float(os.getenv("ENTITY_VECTOR_MIN_SCORE", "0.02")),
                   run_max_workers=int(os.getenv("RUN_MAX_WORKERS", "4")),
                   run_timeout_seconds=float(os.getenv("RUN_TIMEOUT_SECONDS", "300")),
                   run_registry_path=os.getenv("RUN_REGISTRY_PATH", ".runtime/runs.sqlite"),
                   kg_workflow_registry_path=os.getenv("KG_WORKFLOW_REGISTRY_PATH", ".runtime/kg-workflow.sqlite"),
                   tool_transport=os.getenv("TOOL_TRANSPORT", "local").lower(),
                   mcp_server_url=os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8100/mcp"),
                   mcp_request_timeout=float(os.getenv("MCP_REQUEST_TIMEOUT", "30")),
                   mcp_server_host=os.getenv("MCP_SERVER_HOST", "127.0.0.1"),
                   mcp_server_port=int(os.getenv("MCP_SERVER_PORT", "8100")),
                   mcp_server_path=os.getenv("MCP_SERVER_PATH", "/mcp"),
                   mcp_servers=parse_mcp_servers(os.getenv("MCP_SERVERS_JSON")),
                   tool_transport_overrides=parse_transport_overrides(
                       os.getenv("TOOL_TRANSPORT_OVERRIDES_JSON")
                   ),
                   web_search_provider=os.getenv("WEB_SEARCH_PROVIDER", "disabled").lower(),
                   web_search_api_key=os.getenv("WEB_SEARCH_API_KEY"),
                   web_search_endpoint=os.getenv("WEB_SEARCH_ENDPOINT"),
                   web_search_timeout=float(os.getenv("WEB_SEARCH_TIMEOUT", "15")),
                   web_search_max_results=int(os.getenv("WEB_SEARCH_MAX_RESULTS", "5")),
                   observability_db_path=os.getenv("OBSERVABILITY_DB_PATH", ".runtime/observability.sqlite"),
                   memory_backend=os.getenv("MEMORY_BACKEND", "sqlite").lower(),
                   memory_mysql_database=os.getenv("MEMORY_MYSQL_DATABASE", "gkx_runtime"),
                   conversation_memory_db_path=os.getenv("CONVERSATION_MEMORY_DB_PATH", ".runtime/conversation-memory.sqlite"),
                   query_experience_db_path=os.getenv("QUERY_EXPERIENCE_DB_PATH", ".runtime/query-experience.sqlite"),
                   long_term_memory_db_path=os.getenv("LONG_TERM_MEMORY_DB_PATH", ".runtime/long-term-memory.sqlite"),
                   memory_extraction_enabled=os.getenv("MEMORY_EXTRACTION_ENABLED", "true").lower() == "true",
                   memory_worker_poll_seconds=max(0.1, float(os.getenv("MEMORY_WORKER_POLL_SECONDS", "1"))),
                   memory_worker_batch_size=max(1, min(int(os.getenv("MEMORY_WORKER_BATCH_SIZE", "10")), 100)),
                   memory_worker_lease_seconds=max(5, int(os.getenv("MEMORY_WORKER_LEASE_SECONDS", "60"))),
                   memory_worker_max_attempts=max(1, int(os.getenv("MEMORY_WORKER_MAX_ATTEMPTS", "3"))),
                   memory_fact_confidence_threshold=max(0.0, min(float(os.getenv("MEMORY_FACT_CONFIDENCE_THRESHOLD", "0.85")), 1.0)),
                   memory_fact_max_per_scope=max(10, min(int(os.getenv("MEMORY_FACT_MAX_PER_SCOPE", "100")), 1000)),
                   memory_fact_review_days=max(1, min(int(os.getenv("MEMORY_FACT_REVIEW_DAYS", "90")), 3650)),
                   memory_fact_similarity_threshold=max(0.5, min(float(os.getenv("MEMORY_FACT_SIMILARITY_THRESHOLD", "0.86")), 0.99)),
                   memory_recall_top_k=max(3, min(int(os.getenv("MEMORY_RECALL_TOP_K", "5")), 5)),
                   memory_recall_candidate_limit=max(5, min(int(os.getenv("MEMORY_RECALL_CANDIDATE_LIMIT", "100")), 500)),
                   memory_recall_token_budget=max(800, min(int(os.getenv("MEMORY_RECALL_TOKEN_BUDGET", "1000")), 1200)),
                   memory_milvus_uri=os.getenv("MEMORY_MILVUS_URI", ".runtime/user-memory-milvus.db"),
                   memory_milvus_collection=os.getenv("MEMORY_MILVUS_COLLECTION", "user_memory_facts_v1"),
                   memory_retrieval_backend=os.getenv("MEMORY_RETRIEVAL_BACKEND", "mysql").lower(),
                   query_experience_mode=os.getenv("QUERY_EXPERIENCE_MODE", "shadow").lower(),
                   query_experience_scope_id=os.getenv("QUERY_EXPERIENCE_SCOPE_ID", "local"),
                   query_experience_candidate_limit=int(os.getenv("QUERY_EXPERIENCE_CANDIDATE_LIMIT", "5")),
                   query_experience_min_samples=int(os.getenv("QUERY_EXPERIENCE_MIN_SAMPLES", "5")),
                   query_experience_min_similarity=float(os.getenv("QUERY_EXPERIENCE_MIN_SIMILARITY", "0.72")),
                   query_experience_min_confidence=float(os.getenv("QUERY_EXPERIENCE_MIN_CONFIDENCE", "0.75")),
                   workflow_version=os.getenv("WORKFLOW_VERSION", "stage12-runtime-skills"),
                   prompt_version=os.getenv("PROMPT_VERSION", "prompt-v1"),
                   model_input_cost_per_million=float(os.getenv("MODEL_INPUT_COST_PER_MILLION", "0")),
                   model_output_cost_per_million=float(os.getenv("MODEL_OUTPUT_COST_PER_MILLION", "0")),
                   model_cost_currency=os.getenv("MODEL_COST_CURRENCY", "USD"))
        settings.validate_memory_settings()
        return settings
