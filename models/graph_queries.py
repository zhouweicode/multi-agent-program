"""受限图查询 DSL；禁止 Agent 直接提交 Cypher。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

GraphDirection = Literal["in", "out", "both"]
GraphFilterOperator = Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "contains"]


class GraphFilter(BaseModel):
    scope: Literal["source", "relation", "target"] = "target"
    field: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
    operator: GraphFilterOperator = "eq"
    value: Any

    @model_validator(mode="after")
    def validate_value(self) -> GraphFilter:
        if self.operator == "in" and not isinstance(self.value, list):
            raise ValueError("in 操作符的 value 必须是数组")
        return self


class FilteredNeighborsInput(BaseModel):
    entity_id: str = Field(min_length=1, max_length=256)
    relation_types: list[str] = Field(default_factory=list, max_length=20)
    target_labels: list[str] = Field(default_factory=list, max_length=10)
    direction: GraphDirection = "both"
    start_year: int | None = Field(default=None, ge=1800, le=2200)
    end_year: int | None = Field(default=None, ge=1800, le=2200)
    min_weight: float | None = Field(default=None, ge=0)
    filters: list[GraphFilter] = Field(default_factory=list, max_length=10)
    limit: int = Field(default=50, ge=1, le=100)

    @model_validator(mode="after")
    def validate_years(self) -> FilteredNeighborsInput:
        if self.start_year and self.end_year and self.start_year > self.end_year:
            raise ValueError("start_year 不能大于 end_year")
        return self


class FindPathsInput(BaseModel):
    source_id: str = Field(min_length=1, max_length=256)
    target_id: str = Field(min_length=1, max_length=256)
    max_hops: int = Field(default=4, ge=1, le=6)
    top_k: int = Field(default=3, ge=1, le=10)
    relation_types: list[str] = Field(default_factory=list, max_length=20)
    direction: GraphDirection = "both"
    ranking: Literal["shortest", "weight"] = "shortest"
    min_weight: float | None = Field(default=None, ge=0)


class QuerySubgraphInput(BaseModel):
    seed_entity_ids: list[str] = Field(min_length=1, max_length=20)
    max_hops: int = Field(default=2, ge=1, le=3)
    node_labels: list[str] = Field(default_factory=list, max_length=20)
    relation_types: list[str] = Field(default_factory=list, max_length=20)
    direction: GraphDirection = "both"
    max_nodes: int = Field(default=100, ge=1, le=200)
    max_edges: int = Field(default=200, ge=1, le=500)


class GraphFieldRef(BaseModel):
    scope: Literal["source", "relation", "target"]
    field: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
    alias: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


class GraphMetric(BaseModel):
    operation: Literal["count", "count_distinct", "sum", "avg", "min", "max"]
    field: GraphFieldRef | None = None
    alias: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")

    @model_validator(mode="after")
    def validate_field(self) -> GraphMetric:
        if self.operation != "count" and self.field is None:
            raise ValueError(f"{self.operation} 必须声明 field")
        return self


class GraphOrderBy(BaseModel):
    field: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
    direction: Literal["asc", "desc"] = "desc"


class AggregateGraphInput(BaseModel):
    source_label: str
    relation_type: str | None = None
    target_label: str | None = None
    direction: GraphDirection = "both"
    filters: list[GraphFilter] = Field(default_factory=list, max_length=10)
    group_by: list[GraphFieldRef] = Field(default_factory=list, max_length=5)
    metrics: list[GraphMetric] = Field(min_length=1, max_length=10)
    order_by: list[GraphOrderBy] = Field(default_factory=list, max_length=5)
    limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def validate_shape(self) -> AggregateGraphInput:
        if self.target_label and not self.relation_type:
            raise ValueError("target_label 必须与 relation_type 一起使用")
        aliases = [
            item.alias or f"{item.scope}_{item.field}" for item in self.group_by
        ] + [item.alias for item in self.metrics]
        if len(aliases) != len(set(aliases)):
            raise ValueError("group_by 和 metrics 的 alias 必须唯一")
        unknown_orders = {item.field for item in self.order_by} - set(aliases)
        if unknown_orders:
            raise ValueError(
                "order_by 只能引用返回 alias: " + ", ".join(sorted(unknown_orders))
            )
        if not self.relation_type and any(
            item.scope != "source"
            for item in self.group_by
            + [metric.field for metric in self.metrics if metric.field]
            + self.filters
        ):
            raise ValueError("无 relation_type 时只能引用 source 字段")
        return self
