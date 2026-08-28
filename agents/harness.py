"""可复用的 Agent Harness：统一模型循环、Middleware、预算与工具执行策略。"""

from __future__ import annotations

import json
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar

from langchain_core.messages import ToolMessage

from models.schemas import ToolCallSpec
from models.settings import Settings
from services.observability import emit_event
from services.run_control import raise_if_stopped
from services.telemetry import traced_span
from tools.governance import build_tool_receipt, sanitize_remote_result


def _env_prefix(agent_name: str) -> str:
    return "".join(
        character if character.isalnum() else "_" for character in agent_name.upper()
    )


@dataclass(frozen=True)
class HarnessConfig:
    max_steps: int = 12
    max_tool_calls: int = 16
    max_duration_seconds: float = 120
    max_tokens: int = 0
    max_cost: float = 0
    tool_timeout_seconds: float = 30
    tool_max_retries: int = 1
    observation_max_chars: int = 8_000
    observation_max_items: int = 50
    loop_repeat_threshold: int = 3

    @classmethod
    def from_env(cls, agent_name: str, **defaults: Any) -> HarnessConfig:
        """全局 AGENT_* 配置可被 `<AGENT_NAME>_*` 精确覆盖。"""
        import os

        prefix = _env_prefix(agent_name)

        def value(name: str, default: Any, cast: type) -> Any:
            raw = os.getenv(f"{prefix}_{name}", os.getenv(f"AGENT_{name}"))
            return default if raw is None else cast(raw)

        base = cls(**defaults)
        return cls(
            max_steps=value("MAX_STEPS", base.max_steps, int),
            max_tool_calls=value("MAX_TOOL_CALLS", base.max_tool_calls, int),
            max_duration_seconds=value(
                "MAX_DURATION_SECONDS", base.max_duration_seconds, float
            ),
            max_tokens=value("MAX_TOKENS", base.max_tokens, int),
            max_cost=value("MAX_COST", base.max_cost, float),
            tool_timeout_seconds=value(
                "TOOL_TIMEOUT_SECONDS", base.tool_timeout_seconds, float
            ),
            tool_max_retries=value("TOOL_MAX_RETRIES", base.tool_max_retries, int),
            observation_max_chars=value(
                "OBSERVATION_MAX_CHARS", base.observation_max_chars, int
            ),
            observation_max_items=value(
                "OBSERVATION_MAX_ITEMS", base.observation_max_items, int
            ),
            loop_repeat_threshold=value(
                "LOOP_REPEAT_THRESHOLD", base.loop_repeat_threshold, int
            ),
        )


@dataclass
class HarnessMetrics:
    steps: int = 0
    tool_calls: int = 0
    tool_attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0
    duration_ms: float = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "steps": self.steps,
            "tool_calls": self.tool_calls,
            "tool_attempts": self.tool_attempts,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost": round(self.cost, 8),
            "duration_ms": round(self.duration_ms, 2),
        }


@dataclass
class HarnessContext:
    agent_name: str
    thread_id: str | None
    config: HarnessConfig
    started_at: float = field(default_factory=time.perf_counter)
    metrics: HarnessMetrics = field(default_factory=HarnessMetrics)
    fingerprints: list[str] = field(default_factory=list)

    def elapsed_seconds(self) -> float:
        return time.perf_counter() - self.started_at


@dataclass
class HarnessRunResult:
    final_response: str | None
    tool_calls: list[ToolCallSpec]
    observations: list[dict[str, Any]]
    errors: list[str]
    metrics: dict[str, Any]
    stop_reason: str
    messages: list[Any]


class HarnessControlError(RuntimeError):
    code = "HARNESS_STOPPED"


class BudgetExceededError(HarnessControlError):
    code = "AGENT_BUDGET_EXCEEDED"


class LoopDetectedError(HarnessControlError):
    code = "AGENT_LOOP_DETECTED"


class HarnessMiddleware:
    """Middleware 扩展点；实现方只需覆盖关心的 hook。"""

    def before_run(self, context: HarnessContext, messages: list[Any]) -> None:
        pass

    def before_model(self, context: HarnessContext, messages: list[Any]) -> None:
        pass

    def after_model(self, context: HarnessContext, message: Any) -> None:
        pass

    def before_tool(self, context: HarnessContext, call: dict[str, Any]) -> None:
        pass

    def after_tool(
        self, context: HarnessContext, call: dict[str, Any], observation: dict[str, Any]
    ) -> None:
        pass

    def on_tool_error(
        self, context: HarnessContext, call: dict[str, Any], observation: dict[str, Any]
    ) -> None:
        pass

    def after_run(self, context: HarnessContext, result: HarnessRunResult) -> None:
        pass


class MiddlewareChain:
    def __init__(self, middleware: list[HarnessMiddleware] | None = None):
        self.middleware = list(middleware or [])

    def call(self, hook: str, *args: Any) -> None:
        for item in self.middleware:
            getattr(item, hook)(*args)


class RunControlMiddleware(HarnessMiddleware):
    def before_run(self, context: HarnessContext, messages: list[Any]) -> None:
        raise_if_stopped(context.thread_id)

    def before_model(self, context: HarnessContext, messages: list[Any]) -> None:
        raise_if_stopped(context.thread_id)

    def before_tool(self, context: HarnessContext, call: dict[str, Any]) -> None:
        raise_if_stopped(context.thread_id)


class BudgetMiddleware(HarnessMiddleware):
    @staticmethod
    def _check(context: HarnessContext) -> None:
        config, metrics = context.config, context.metrics
        if (
            config.max_duration_seconds > 0
            and context.elapsed_seconds() >= config.max_duration_seconds
        ):
            raise BudgetExceededError(
                f"耗时预算已超限: {config.max_duration_seconds:g}s"
            )
        if config.max_tokens > 0 and metrics.total_tokens > config.max_tokens:
            raise BudgetExceededError(f"Token 预算已超限: {config.max_tokens}")
        if config.max_cost > 0 and metrics.cost > config.max_cost:
            raise BudgetExceededError(f"成本预算已超限: {config.max_cost:g}")

    def before_model(self, context: HarnessContext, messages: list[Any]) -> None:
        self._check(context)

    def after_model(self, context: HarnessContext, message: Any) -> None:
        self._check(context)

    def before_tool(self, context: HarnessContext, call: dict[str, Any]) -> None:
        self._check(context)


class LoopDetectionMiddleware(HarnessMiddleware):
    """检测重复调用和 A-B-A-B 型工具循环。"""

    def before_tool(self, context: HarnessContext, call: dict[str, Any]) -> None:
        fingerprint = json.dumps(
            {"name": call.get("name"), "args": call.get("args", {})},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        history = context.fingerprints
        threshold = max(2, context.config.loop_repeat_threshold)
        if history.count(fingerprint) + 1 >= threshold:
            raise LoopDetectedError(f"检测到重复工具调用: {call.get('name')}")
        candidate = history + [fingerprint]
        if len(candidate) >= threshold * 2:
            tail = candidate[-threshold * 2 :]
            if len(set(tail)) == 2 and all(
                tail[index] == tail[index % 2] for index in range(len(tail))
            ):
                raise LoopDetectedError("检测到交替工具调用循环")
        history.append(fingerprint)


class ToolGovernanceMiddleware(HarnessMiddleware):
    """规范工具身份、清洗远程内容并生成不含原文的调用回执。"""

    def __init__(self, tools: dict[str, Any]):
        self.tools = tools

    def _govern(self, call: dict[str, Any], observation: dict[str, Any]) -> None:
        visible_name = str(call.get("name") or "unknown")
        tool = self.tools.get(visible_name)
        metadata = dict(getattr(tool, "metadata", None) or {})
        canonical_name = str(metadata.get("canonical_tool_name") or visible_name)
        should_sanitize = (
            metadata.get("tool_transport") == "mcp"
            or metadata.get("trust_level") == "remote_content"
        )
        sanitization = None
        raw_output = observation.get("data")
        if should_sanitize:
            observation["data"], sanitization = sanitize_remote_result(
                observation.get("data")
            )
        observation["tool"] = canonical_name
        observation["visible_tool"] = visible_name
        observation["source_metadata"] = {
            "transport": metadata.get("tool_transport", "local"),
            "source": metadata.get("tool_source", "local:repository"),
            "server_name": metadata.get("mcp_server_name"),
            "trust_level": metadata.get("trust_level", "internal"),
        }
        observation["receipt"] = build_tool_receipt(
            visible_name=visible_name,
            canonical_name=canonical_name,
            arguments=call.get("args", {}),
            output=observation.get("data"),
            raw_output=raw_output,
            success=bool(observation.get("success")),
            attempts=int(observation.get("attempts") or 0),
            duration_ms=float(observation.get("duration_ms") or 0),
            metadata=metadata,
            sanitization=sanitization,
        )

    def after_tool(
        self, context: HarnessContext, call: dict[str, Any], observation: dict[str, Any]
    ) -> None:
        self._govern(call, observation)

    def on_tool_error(
        self, context: HarnessContext, call: dict[str, Any], observation: dict[str, Any]
    ) -> None:
        self._govern(call, observation)


class ToolErrorCategory(str, Enum):
    UNAUTHORIZED = "UNAUTHORIZED"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    TIMEOUT = "TIMEOUT"
    RATE_LIMIT = "RATE_LIMIT"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    TOOL_ERROR = "TOOL_ERROR"


@dataclass
class ToolExecutionResult:
    success: bool
    output: Any
    attempts: int
    duration_ms: float
    category: ToolErrorCategory | None = None
    message: str | None = None


def classify_tool_error(exc: Exception) -> ToolErrorCategory:
    message = str(exc).lower()
    status = getattr(exc, "status_code", None)
    name = type(exc).__name__.lower()
    if (
        isinstance(exc, TimeoutError)
        or "timeout" in name
        or "timed out" in message
        or "超时" in message
    ):
        return ToolErrorCategory.TIMEOUT
    if status == 429 or "rate limit" in message or "too many requests" in message:
        return ToolErrorCategory.RATE_LIMIT
    if status in {502, 503, 504} or isinstance(exc, (ConnectionError, OSError)):
        return ToolErrorCategory.PROVIDER_UNAVAILABLE
    if isinstance(exc, (TypeError, ValueError, KeyError)):
        return ToolErrorCategory.INVALID_ARGUMENT
    return ToolErrorCategory.TOOL_ERROR


def _invoke_with_timeout(tool: Any, arguments: dict[str, Any], timeout: float) -> Any:
    if timeout <= 0:
        return tool.invoke(arguments)
    result_queue: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

    def target() -> None:
        try:
            result_queue.put((True, tool.invoke(arguments)))
        except Exception as exc:  # noqa: BLE001 - 在线程边界传回工具错误，由主线程统一分类。
            result_queue.put((False, exc))

    threading.Thread(
        target=target, name=f"tool-{getattr(tool, 'name', 'unknown')}", daemon=True
    ).start()
    try:
        success, value = result_queue.get(timeout=timeout)
    except queue.Empty as exc:
        raise TimeoutError(f"工具执行超过 {timeout:g}s") from exc
    if success:
        return value
    raise value


class ToolExecutor:
    RETRYABLE: ClassVar[set[ToolErrorCategory]] = {
        ToolErrorCategory.TIMEOUT,
        ToolErrorCategory.RATE_LIMIT,
        ToolErrorCategory.PROVIDER_UNAVAILABLE,
    }

    def execute(
        self,
        tool: Any,
        arguments: dict[str, Any],
        config: HarnessConfig,
        on_retry: Callable[[ToolErrorCategory, int, Exception], None] | None = None,
    ) -> ToolExecutionResult:
        started = time.perf_counter()
        attempts = 0
        last_error: Exception | None = None
        last_category: ToolErrorCategory | None = None
        for attempts in range(1, max(0, config.tool_max_retries) + 2):
            try:
                output = _invoke_with_timeout(
                    tool, arguments, config.tool_timeout_seconds
                )
                return ToolExecutionResult(
                    True, output, attempts, (time.perf_counter() - started) * 1000
                )
            except Exception as exc:  # noqa: BLE001 - ToolExecutor 是工具故障分类边界。
                last_error = exc
                last_category = classify_tool_error(exc)
                if (
                    last_category not in self.RETRYABLE
                    or attempts > config.tool_max_retries
                ):
                    break
                if on_retry:
                    on_retry(last_category, attempts, exc)
        return ToolExecutionResult(
            False,
            {
                "error": last_category.value
                if last_category
                else ToolErrorCategory.TOOL_ERROR.value,
                "message": str(last_error),
            },
            attempts,
            (time.perf_counter() - started) * 1000,
            last_category,
            str(last_error),
        )


def _message_text(content: Any) -> str | None:
    if isinstance(content, str):
        return content.strip() or None
    if isinstance(content, list):
        parts = [
            str(item.get("text", "")) for item in content if isinstance(item, dict)
        ]
        return "\n".join(part for part in parts if part).strip() or None
    return str(content).strip() or None if content is not None else None


def compress_observation(
    value: Any, max_chars: int, max_items: int
) -> tuple[Any, bool, int]:
    encoded = json.dumps(value, ensure_ascii=False, default=str)
    if max_chars <= 0 or len(encoded) <= max_chars:
        return value, False, len(encoded)

    def prune(item: Any) -> Any:
        if isinstance(item, list):
            values = [prune(child) for child in item[:max_items]]
            if len(item) > max_items:
                values.append({"_truncated_items": len(item) - max_items})
            return values
        if isinstance(item, dict):
            pairs = list(item.items())
            result = {key: prune(child) for key, child in pairs[:max_items]}
            if len(pairs) > max_items:
                result["_truncated_keys"] = len(pairs) - max_items
            return result
        if isinstance(item, str) and len(item) > max_chars // 2:
            return item[: max(32, max_chars // 2)] + "…"
        return item

    compact = prune(value)
    compact_encoded = json.dumps(compact, ensure_ascii=False, default=str)
    if len(compact_encoded) <= max_chars:
        return compact, True, len(encoded)
    return (
        {
            "_observation_compressed": True,
            "original_type": type(value).__name__,
            "original_chars": len(encoded),
            "preview": compact_encoded[: max(32, max_chars - 160)],
        },
        True,
        len(encoded),
    )


class AgentHarness:
    """独立于业务结果协议的通用 Tool Calling 执行器。"""

    def __init__(
        self,
        name: str,
        model: Any,
        tools: list[Any],
        *,
        config: HarnessConfig | None = None,
        middleware: list[HarnessMiddleware] | None = None,
        tool_executor: ToolExecutor | None = None,
    ):
        self.name = name
        self.tools = {item.name: item for item in tools}
        self.model = model.bind_tools(tools)
        self.config = config or HarnessConfig.from_env(name)
        defaults = [
            RunControlMiddleware(),
            BudgetMiddleware(),
            LoopDetectionMiddleware(),
        ]
        self.middleware = MiddlewareChain(
            defaults + list(middleware or []) + [ToolGovernanceMiddleware(self.tools)]
        )
        self.tool_executor = tool_executor or ToolExecutor()

    @staticmethod
    def _usage(message: Any) -> tuple[int, int, int]:
        usage = getattr(message, "usage_metadata", None) or {}
        if not usage:
            metadata = getattr(message, "response_metadata", None) or {}
            usage = metadata.get("token_usage") or metadata.get("usage") or {}
        input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
        output_tokens = int(
            usage.get("output_tokens") or usage.get("completion_tokens") or 0
        )
        total_tokens = int(usage.get("total_tokens") or input_tokens + output_tokens)
        return input_tokens, output_tokens, total_tokens

    def _record_usage(self, context: HarnessContext, message: Any) -> None:
        input_tokens, output_tokens, total_tokens = self._usage(message)
        metrics = context.metrics
        metrics.input_tokens += input_tokens
        metrics.output_tokens += output_tokens
        metrics.total_tokens += total_tokens
        model_config = Settings.from_env().model_config(self.name)
        metrics.cost += (
            input_tokens * model_config.input_cost_per_million
            + output_tokens * model_config.output_cost_per_million
        ) / 1_000_000

    @staticmethod
    def _budget_event(context: HarnessContext) -> None:
        context.metrics.duration_ms = context.elapsed_seconds() * 1000
        emit_event(
            "AGENT_BUDGET_UPDATED",
            thread_id=context.thread_id,
            agent_name=context.agent_name,
            **context.metrics.as_dict(),
        )

    def execute(
        self, messages: list[Any], thread_id: str | None = None
    ) -> HarnessRunResult:
        context = HarnessContext(self.name, thread_id, self.config)
        calls: list[ToolCallSpec] = []
        observations: list[dict[str, Any]] = []
        errors: list[str] = []
        final_response = None
        stop_reason = "completed"
        active_messages = list(messages)
        emit_event(
            "AGENT_STARTED",
            thread_id=thread_id,
            agent_name=self.name,
            max_steps=self.config.max_steps,
            max_tool_calls=self.config.max_tool_calls,
        )
        try:
            self.middleware.call("before_run", context, active_messages)
            for step in range(1, self.config.max_steps + 1):
                context.metrics.steps = step
                try:
                    self.middleware.call("before_model", context, active_messages)
                    emit_event(
                        "AGENT_MODEL_STARTED",
                        thread_id=thread_id,
                        agent_name=self.name,
                        step=step,
                    )
                    started = time.perf_counter()
                    with traced_span(
                        "agent.model.invoke",
                        "model_operation",
                        {
                            "agent.name": self.name,
                            "agent.step": step,
                        },
                    ):
                        message = self.model.invoke(active_messages)
                    duration_ms = (time.perf_counter() - started) * 1000
                    active_messages.append(message)
                    self._record_usage(context, message)
                    emit_event(
                        "AGENT_MODEL_COMPLETED",
                        thread_id=thread_id,
                        agent_name=self.name,
                        step=step,
                        duration_ms=round(duration_ms, 2),
                        tool_call_count=len(message.tool_calls or []),
                    )
                    self._budget_event(context)
                    self.middleware.call("after_model", context, message)
                except BudgetExceededError as exc:
                    errors.append(str(exc))
                    stop_reason = exc.code
                    emit_event(
                        exc.code,
                        thread_id=thread_id,
                        agent_name=self.name,
                        step=step,
                        error=str(exc),
                    )
                    break

                if not message.tool_calls:
                    final_response = _message_text(message.content)
                    break

                loop_stopped = False
                for call in message.tool_calls:
                    if len(calls) >= self.config.max_tool_calls:
                        output = {
                            "error": "TOOL_CALL_BUDGET_EXCEEDED",
                            "message": f"整个任务最多调用 {self.config.max_tool_calls} 次工具，请基于已有结果作答",
                        }
                        if not any("工具调用预算" in item for item in errors):
                            errors.append(
                                f"工具调用预算已达上限: {self.config.max_tool_calls}"
                            )
                        active_messages.append(
                            ToolMessage(
                                content=json.dumps(output, ensure_ascii=False),
                                tool_call_id=call["id"],
                                name=call["name"],
                            )
                        )
                        continue

                    selected_tool = self.tools.get(call["name"])
                    selected_metadata = dict(
                        getattr(selected_tool, "metadata", None) or {}
                    )
                    canonical_name = str(
                        selected_metadata.get("canonical_tool_name") or call["name"]
                    )
                    calls.append(
                        ToolCallSpec(
                            name=canonical_name, arguments=call.get("args", {})
                        )
                    )
                    context.metrics.tool_calls = len(calls)
                    try:
                        self.middleware.call("before_tool", context, call)
                    except (BudgetExceededError, LoopDetectedError) as exc:
                        output = {"error": exc.code, "message": str(exc)}
                        errors.append(str(exc))
                        observations.append(
                            {
                                "tool": call["name"],
                                "data": output,
                                "success": False,
                                "error_category": exc.code,
                                "attempts": 0,
                            }
                        )
                        active_messages.append(
                            ToolMessage(
                                content=json.dumps(output, ensure_ascii=False),
                                tool_call_id=call["id"],
                                name=call["name"],
                            )
                        )
                        stop_reason = exc.code
                        emit_event(
                            exc.code,
                            thread_id=thread_id,
                            agent_name=self.name,
                            tool_name=call["name"],
                            step=step,
                            error=str(exc),
                        )
                        loop_stopped = True
                        break

                    tool = selected_tool
                    emit_event(
                        "AGENT_TOOL_CALLED",
                        thread_id=thread_id,
                        agent_name=self.name,
                        tool_name=call["name"],
                        step=step,
                        tool_input=call.get("args", {}),
                    )
                    if tool is None:
                        execution = ToolExecutionResult(
                            False,
                            {
                                "error": ToolErrorCategory.UNAUTHORIZED.value,
                                "message": f"工具未授权: {call['name']}",
                            },
                            0,
                            0,
                            ToolErrorCategory.UNAUTHORIZED,
                            f"工具未授权: {call['name']}",
                        )
                    else:
                        metadata = getattr(tool, "metadata", None) or {}
                        with traced_span(
                            f"tool.{call['name']}",
                            "tool",
                            {
                                "agent.name": self.name,
                                "tool.name": call["name"],
                                "tool.transport": metadata.get(
                                    "tool_transport", "local"
                                ),
                                "agent.step": step,
                            },
                        ) as tool_span:
                            execution = self.tool_executor.execute(
                                tool,
                                call.get("args", {}),
                                self.config,
                                on_retry=lambda category, attempt, exc, tool_name=call["name"], current_step=step: (
                                    emit_event(
                                        "AGENT_TOOL_RETRYING",
                                        thread_id=thread_id,
                                        agent_name=self.name,
                                        tool_name=tool_name,
                                        step=current_step,
                                        failed_attempt=attempt,
                                        error_category=category.value,
                                        error=str(exc),
                                    )
                                ),
                            )
                            tool_span.set_attribute("tool.attempts", execution.attempts)
                            tool_span.set_attribute(
                                "tool.result_count",
                                len(execution.output)
                                if isinstance(execution.output, list)
                                else 1,
                            )
                    context.metrics.tool_attempts += execution.attempts
                    observation = {
                        "tool": call["name"],
                        "data": execution.output,
                        "success": execution.success,
                        "error_category": execution.category.value
                        if execution.category
                        else None,
                        "attempts": execution.attempts,
                        "duration_ms": round(execution.duration_ms, 2),
                    }
                    observations.append(observation)
                    if execution.success:
                        self.middleware.call("after_tool", context, call, observation)
                        emit_event(
                            "AGENT_TOOL_COMPLETED",
                            thread_id=thread_id,
                            agent_name=self.name,
                            tool_name=call["name"],
                            step=step,
                            attempts=execution.attempts,
                            duration_ms=round(execution.duration_ms, 2),
                            result_count=len(observation["data"])
                            if isinstance(observation["data"], list)
                            else 1,
                            canonical_tool_name=observation["tool"],
                            tool_source=observation["source_metadata"]["source"],
                            receipt_id=observation["receipt"]["receipt_id"],
                            tool_output=observation["data"],
                        )
                    else:
                        category = execution.category or ToolErrorCategory.TOOL_ERROR
                        self.middleware.call(
                            "on_tool_error", context, call, observation
                        )
                        safe_data = observation.get("data")
                        safe_message = (
                            safe_data.get("message")
                            if isinstance(safe_data, dict)
                            else str(safe_data)
                        ) or execution.message
                        errors.append(
                            f"{call['name']}: [{category.value}] {safe_message}"
                        )
                        event = (
                            "AGENT_TOOL_TIMED_OUT"
                            if category == ToolErrorCategory.TIMEOUT
                            else "AGENT_TOOL_FAILED"
                        )
                        emit_event(
                            event,
                            thread_id=thread_id,
                            agent_name=self.name,
                            tool_name=call["name"],
                            step=step,
                            attempts=execution.attempts,
                            error_category=category.value,
                            error=safe_message,
                            canonical_tool_name=observation["tool"],
                            tool_source=observation["source_metadata"]["source"],
                            receipt_id=observation["receipt"]["receipt_id"],
                        )

                    compressed, was_compressed, original_chars = compress_observation(
                        observation["data"],
                        self.config.observation_max_chars,
                        self.config.observation_max_items,
                    )
                    if was_compressed:
                        emit_event(
                            "AGENT_OBSERVATION_COMPRESSED",
                            thread_id=thread_id,
                            agent_name=self.name,
                            tool_name=call["name"],
                            original_chars=original_chars,
                            compressed_chars=len(
                                json.dumps(compressed, ensure_ascii=False, default=str)
                            ),
                        )
                    active_messages.append(
                        ToolMessage(
                            content=json.dumps(
                                compressed, ensure_ascii=False, default=str
                            ),
                            tool_call_id=call["id"],
                            name=call["name"],
                        )
                    )
                    self._budget_event(context)
                emit_event(
                    "AGENT_STEP_COMPLETED",
                    thread_id=thread_id,
                    agent_name=self.name,
                    step=step,
                    cumulative_tool_calls=len(calls),
                    stop_reason=stop_reason if loop_stopped else None,
                )
                if loop_stopped:
                    break
            else:
                errors.append(f"达到最大工具调用轮数: {self.config.max_steps}")
                stop_reason = "MAX_STEPS_EXCEEDED"
        finally:
            context.metrics.duration_ms = context.elapsed_seconds() * 1000

        result = HarnessRunResult(
            final_response,
            calls,
            observations,
            errors,
            context.metrics.as_dict(),
            stop_reason,
            active_messages,
        )
        self.middleware.call("after_run", context, result)
        emit_event(
            "AGENT_COMPLETED",
            thread_id=thread_id,
            agent_name=self.name,
            tool_call_count=len(calls),
            error_count=len(errors),
            stop_reason=stop_reason,
            metrics=result.metrics,
        )
        return result
