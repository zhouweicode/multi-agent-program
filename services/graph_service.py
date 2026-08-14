"""TRSGraph/nGQL 的接口占位，第一阶段不实现图推理。"""
class GraphService:
    def health(self) -> dict:
        return {"backend": "mock", "ready": True}

