"""预留 FastAPI 层；交互式消歧接口将在下一阶段扩展。"""
from fastapi import FastAPI
from pydantic import BaseModel
from graph.builder import build_graph

app = FastAPI(title="科技知识图谱 Multi-Agent GraphRAG", version="0.1.0")
graph = build_graph()


class QueryRequest(BaseModel):
    question: str
    thread_id: str


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "stage": 1}


@app.post("/query")
def query(request: QueryRequest) -> dict:
    config = {"configurable": {"thread_id": request.thread_id}}
    result = graph.invoke({"question": request.question, "replan_count": 0, "max_replans": 2}, config=config)
    return result

