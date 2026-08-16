是的，当前 Router 会调用 GLM-5.2 大模型生成这些结构化字段。

我检查了当前实际配置：

```text
provider: openai
model: glm-5.2
base_url: https://open.bigmodel.cn/api/paas/v4/
has_api_key: true
```

## 具体调用链

```text
router_node(state)
    ↓
ModelFactory.structured_model()
    ↓
检查 MODEL_PROVIDER
    ↓
OpenAIStructuredModel(ChatOpenAI)
    ↓
invoke_router(question)
    ↓
GLM-5.2 返回 JSON
    ↓
Pydantic RouterOutput 校验
    ↓
model_dump()
    ↓
写入 GraphRAGState
```

### 1. Router Node 发起调用

在 `nodes/router_node.py`：

```python
def router_node(state: GraphRAGState) -> dict:
    output = ModelFactory.structured_model().invoke_router(
        state["question"]
    )

    output = _apply_domain_guardrail(
        state["question"],
        output
    )

    return output.model_dump()
```

`state["question"]` 就是用户输入的问题。

例如：

```text
张伟发表过哪些论文？
```

## 2. ModelFactory 选择真实模型还是 Mock

在 `models/llm.py`：

```python
@staticmethod
def structured_model() -> Any:
    if Settings.from_env().model_provider == "mock":
        return MockStructuredModel()

    return OpenAIStructuredModel(
        ModelFactory._chat_model()
    )
```

这里存在两条路径：

```text
MODEL_PROVIDER=mock
→ MockStructuredModel
→ 不调用外部大模型

MODEL_PROVIDER=openai
→ OpenAIStructuredModel
→ 调用 OpenAI 兼容的大模型接口
```

你的 `.env` 中是：

```env
MODEL_PROVIDER=auto
MODEL_NAME=glm-5.2
```

`models/settings.py` 对 `auto` 的处理是：

```python
provider = (
    "openai"
    if configured_provider == "auto" and api_key
    else "mock"
)
```

因为当前检测到了 API Key，所以最终配置为：

```text
MODEL_PROVIDER=openai
MODEL_NAME=glm-5.2
```

这里的 `openai` 指的是“使用 OpenAI 兼容协议”，并不代表调用的是 OpenAI 模型。实际请求模型仍然是 GLM-5.2。

## 3. 创建 GLM-5.2 ChatModel

`ModelFactory._chat_model()` 使用 `ChatOpenAI`：

```python
return ChatOpenAI(
    model=settings.model_name,
    api_key=settings.model_api_key,
    base_url=settings.model_base_url,
    temperature=settings.model_temperature,
    timeout=settings.model_request_timeout,
    max_retries=settings.model_max_retries,
)
```

实际配置相当于：

```python
ChatOpenAI(
    model="glm-5.2",
    base_url="https://open.bigmodel.cn/api/paas/v4/",
    ...
)
```

智谱接口兼容 OpenAI API，所以这里可以使用 LangChain 的 `ChatOpenAI` 客户端。

## 4. Pydantic 定义输出结构

Router 必须返回的结构定义在 `models/schemas.py`：

```python
class RouterOutput(BaseModel):
    intent: str = Field(description="用户意图")
    entity_mentions: list[str]
    complexity: Literal["simple", "complex"]
    primary_domain: Literal[
        "talent",
        "achievement",
        "enterprise",
        "industry",
        "graph",
    ]
    requires_verification: bool = False
```

这个 Schema 限制了字段名称和类型。

例如：

- `complexity` 只能是 `simple` 或 `complex`
- `primary_domain` 只能是五个合法领域之一
- `entity_mentions` 必须是字符串列表
- `requires_verification` 必须是布尔值

## 5. Schema 如何交给 GLM-5.2

`OpenAIStructuredModel._invoke_json()` 中执行：

```python
model = self.chat_model.with_structured_output(
    schema,
    method="json_mode",
)
```

并且把 Pydantic Schema 转换成 JSON Schema：

```python
schema.model_json_schema()
```

最终 Prompt 大致相当于：

```text
你是 GraphRAG Router，只做意图分类、实体 mention 提取、
复杂度判断和主领域分类。

领域边界：
- talent：专家画像、任职、教育、同事、校友
- achievement：论文、专利、科研项目、学术合作
- enterprise：企业任职、顾问、企业项目、企业专利
- industry：产业链、产业节点、产业事件
- graph：邻居、多跳路径、间接关系、关系强度

只涉及一个领域时 complexity=simple；
涉及多个领域时 complexity=complex。

你必须只返回合法 JSON，不要输出 Markdown 或解释。

JSON Schema：
{
  "properties": {
    "intent": {"type": "string"},
    "entity_mentions": {
      "type": "array",
      "items": {"type": "string"}
    },
    "complexity": {
      "enum": ["simple", "complex"]
    },
    "primary_domain": {
      "enum": [
        "talent",
        "achievement",
        "enterprise",
        "industry",
        "graph"
      ]
    },
    "requires_verification": {
      "type": "boolean"
    }
  }
}

输入：
{
  "question": "张伟发表过哪些论文？"
}
```

## 6. 模型返回 JSON

GLM-5.2 应该返回类似：

```json
{
  "intent": "查询专家发表的论文",
  "entity_mentions": ["张伟"],
  "complexity": "simple",
  "primary_domain": "achievement",
  "requires_verification": false
}
```

LangChain 再根据 `RouterOutput` 将 JSON 转换成 Pydantic 对象：

```python
RouterOutput(
    intent="查询专家发表的论文",
    entity_mentions=["张伟"],
    complexity="simple",
    primary_domain="achievement",
    requires_verification=False,
)
```

如果模型返回：

```json
{
  "complexity": "medium"
}
```

Pydantic 会拒绝它，因为 `medium` 不属于：

```python
Literal["simple", "complex"]
```

因此，Structured Output 的本质是：

```text
Prompt 中提供 JSON Schema
+ 要求模型只返回 JSON
+ LangChain 解析返回值
+ Pydantic 校验字段和类型
```

## 7. 确定性 Guardrail

模型返回后，还要经过：

```python
_apply_domain_guardrail(question, output)
```

例如问题：

```text
张伟发表过哪些论文？
```

如果 GLM-5.2 错误返回：

```json
{
  "primary_domain": "talent"
}
```

代码会通过关键词：

```python
"achievement": (
    "论文",
    "发表",
    "专利",
    "科研成果",
    "科研项目",
    "学术成果",
)
```

识别出唯一明确领域是 `achievement`，然后执行：

```python
output.model_copy(
    update={"primary_domain": "achievement"}
)
```

因此当前 Router 是：

```text
GLM-5.2 负责语义分类和实体 mention 提取
             +
Pydantic 负责结构和类型校验
             +
Python Guardrail 负责明显领域错误修正
```

## 8. 写回 Shared State

最后：

```python
return output.model_dump()
```

会得到普通字典：

```python
{
    "intent": "查询专家发表的论文",
    "entity_mentions": ["张伟"],
    "complexity": "simple",
    "primary_domain": "achievement",
    "requires_verification": False,
}
```

LangGraph 把这些字段合并到原 State：

```python
{
    "thread_id": "run-xxx",
    "question": "张伟发表过哪些论文？",
    "resolved_entities": {},
    "task_history": [],
    "replan_count": 0,
    "max_replans": 2,

    # Router 新增字段
    "intent": "查询专家发表的论文",
    "entity_mentions": ["张伟"],
    "complexity": "simple",
    "primary_domain": "achievement",
    "requires_verification": False,
}
```

所以答案是：当前运行配置下，Router 的五个字段主要由 GLM-5.2 生成；Pydantic 保证结构合法，Python Guardrail 再修正部分明确的领域分类错误。