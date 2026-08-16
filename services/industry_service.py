"""产业链服务：按配置使用共享 Neo4j Repository 或 Mock 数据。"""
from data.mock_enterprises import COMPANIES
from data.mock_industry import CHAINS, CHAIN_NODES, NODE_EVENTS


class IndustryService:
    def __init__(self, repository=None):
        self.repository = repository
        self.backend = "neo4j" if repository else "mock"

    def health(self) -> dict:
        health = getattr(self.repository, "health", None)
        return health() if health else {"backend": self.backend, "ready": True}

    def get_chain_structure(self, chain_id: str) -> dict:
        if self.repository:
            return self.repository.get_chain_structure(chain_id)
        chain = CHAINS.get(chain_id)
        return ({**chain, "node_details": [CHAIN_NODES[item].copy() for item in chain["nodes"]]}
                if chain else {"error": "CHAIN_NOT_FOUND", "chain_id": chain_id})

    def get_node_companies(self, node_id: str) -> list[dict]:
        if self.repository:
            return self.repository.get_node_companies(node_id)
        ids = set(CHAIN_NODES.get(node_id, {}).get("company_ids", []))
        return [row.copy() for row in COMPANIES if row["company_id"] in ids]

    def get_node_events(self, node_id: str) -> list[dict]:
        if self.repository:
            return self.repository.get_node_events(node_id)
        return [{**row, "source": "mock:industry_events"} for row in NODE_EVENTS if row["node_id"] == node_id]
