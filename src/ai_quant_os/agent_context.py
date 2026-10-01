"""Bounded context assembly from server-declared, permission-checked evidence sources."""

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Awaitable, Callable, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.agent_permissions import READ_TOOLS
from ai_quant_os.agent_registry import AgentRegistry
from ai_quant_os.contracts import _require_aware

ResourceClass = Literal["GENERAL", "TRAINING", "HOLDOUT"]
InputStatus = Literal[
    "AVAILABLE",
    "MISSING",
    "DENIED",
    "STALE",
    "FUTURE",
    "UNTRUSTED",
    "TIMEOUT",
    "FAILED",
    "BUDGET_EXCEEDED",
]


def _json(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=False
    )


class EvidenceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_id: str = Field(min_length=1, max_length=256)
    as_of: AwareDatetime
    valid_until: AwareDatetime
    trusted: bool
    payload_json: str = Field(min_length=2, max_length=65536)

    @model_validator(mode="after")
    def canonical_payload(self) -> "EvidenceRecord":
        if self.valid_until <= self.as_of:
            raise ValueError("Source expiry must follow observation")
        value = json.loads(self.payload_json)
        if not isinstance(value, dict):
            raise ValueError("Context evidence must be a JSON object")
        object.__setattr__(self, "payload_json", _json(value))
        object.__setattr__(self, "as_of", self.as_of.astimezone(timezone.utc))
        object.__setattr__(self, "valid_until", self.valid_until.astimezone(timezone.utc))
        return self

    @property
    def content_ref(self) -> str:
        return "context-evidence:" + sha256(self.model_dump_json().encode()).hexdigest()


@dataclass(frozen=True)
class SourceBinding:
    tool: str
    resource_class: ResourceClass
    fetch: Callable[[], Awaitable[EvidenceRecord]]

    def __post_init__(self) -> None:
        if (
            self.tool not in READ_TOOLS
            or self.resource_class not in {"GENERAL", "TRAINING", "HOLDOUT"}
            or not callable(self.fetch)
        ):
            raise ValueError("Invalid read-only context source binding")


class ContextInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    input_name: str
    status: InputStatus
    resource_class: ResourceClass | None = None
    source_tool: str | None = None
    source_id: str | None = None
    as_of: AwareDatetime | None = None
    valid_until: AwareDatetime | None = None
    content_ref: str | None = None
    payload_json: str | None = None


class CompiledContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    context_ref: str
    agent_id: str
    agent_version: str
    manifest_ref: str
    task_id: str = Field(min_length=1, max_length=128)
    scope: Literal["PAPER", "SANDBOX"]
    status: Literal["READY", "DEGRADED"]
    inputs: tuple[ContextInput, ...]
    input_refs: tuple[str, ...]
    created_at: AwareDatetime
    valid_until: AwareDatetime
    requires_escalation: Literal[True] = True

    @model_validator(mode="after")
    def consistent_inputs(self) -> "CompiledContext":
        names = [row.input_name for row in self.inputs]
        if not 1 <= len(names) <= 32 or len(set(names)) != len(names):
            raise ValueError("Invalid context input names")
        ready = all(row.status == "AVAILABLE" for row in self.inputs)
        if ready != (self.status == "READY"):
            raise ValueError("Context status does not match available inputs")
        return self


def _context_ref(context: CompiledContext) -> str:
    raw = _json(context.model_dump(mode="json", exclude={"context_ref"}))
    return "agent-context:" + sha256(raw.encode()).hexdigest()


def verify_context(context: CompiledContext) -> None:
    try:
        context = CompiledContext.model_validate_json(context.model_dump_json())
    except ValueError as error:
        raise ValueError("Context integrity schema mismatch") from error
    if context.context_ref != _context_ref(context):
        raise ValueError("Context integrity verification failed")
    if not context.created_at < context.valid_until <= context.created_at + timedelta(seconds=5):
        raise ValueError("Context integrity validity mismatch")
    available = tuple(row for row in context.inputs if row.status == "AVAILABLE")
    if context.input_refs != tuple(row.content_ref for row in available):
        raise ValueError("Context integrity reference mismatch")
    for row in context.inputs:
        if row.status == "AVAILABLE":
            if row.source_tool not in READ_TOOLS or row.resource_class is None:
                raise ValueError("Context integrity source capability mismatch")
            record = EvidenceRecord(
                source_id=row.source_id,
                as_of=row.as_of,
                valid_until=row.valid_until,
                trusted=True,
                payload_json=row.payload_json,
            )
            if (
                record.content_ref != row.content_ref
                or not record.as_of
                <= context.created_at
                < context.valid_until
                <= record.valid_until
            ):
                raise ValueError("Context integrity source mismatch")
        elif any(
            value is not None
            for value in (
                row.source_id,
                row.as_of,
                row.valid_until,
                row.content_ref,
                row.payload_json,
                row.resource_class,
                row.source_tool,
            )
        ):
            raise ValueError("Context integrity unavailable input contains evidence")


def require_current_context(context: CompiledContext, *, now: datetime) -> None:
    _require_aware(now)
    verify_context(context)
    if not context.created_at <= now < context.valid_until:
        raise ValueError("Context is expired or future-dated")


def require_context_for_agent(
    context: CompiledContext,
    *,
    registry: AgentRegistry,
    agent_id: str,
    agent_version: str,
    task_id: str,
    scope: str,
    now: datetime,
) -> None:
    require_current_context(context, now=now)
    manifest = registry.get(agent_id, agent_version)
    if (
        context.agent_id != agent_id
        or context.agent_version != agent_version
        or context.task_id != task_id
        or context.scope != scope
        or context.manifest_ref != manifest.manifest_ref
        or tuple(row.input_name for row in context.inputs) != manifest.required_inputs
        or registry.lifecycle(agent_id, agent_version) == "QUARANTINED"
    ):
        raise PermissionError("Agent context admission denied")
    for row in context.inputs:
        if row.status == "AVAILABLE":
            registry.require_tool(agent_id, agent_version, row.source_tool, scope=scope)
            if row.resource_class in {"TRAINING", "HOLDOUT"}:
                registry.require_tool(
                    agent_id, agent_version, row.resource_class + "_READ", scope=scope
                )


class ContextCompiler:
    def __init__(
        self,
        registry: AgentRegistry,
        sources: dict[str, SourceBinding],
        *,
        clock: Callable[[], datetime] | None = None,
        timeout_seconds: float = 0.1,
        overall_timeout_seconds: float = 2,
        max_concurrency: int = 4,
        max_input_bytes: int = 8192,
        max_context_bytes: int = 32768,
    ) -> None:
        if (
            not 0 < timeout_seconds <= 30
            or not 0 < overall_timeout_seconds <= 30
            or not 1 <= max_concurrency <= 32
        ):
            raise ValueError("Invalid context deadline or concurrency")
        if not 1 <= max_input_bytes <= 65536 or not 4096 <= max_context_bytes <= 1_000_000:
            raise ValueError("Invalid context byte budgets")
        self._registry, self._sources = registry, sources.copy()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._timeout, self._overall = timeout_seconds, overall_timeout_seconds
        self._concurrency = max_concurrency
        self._input_bytes, self._context_bytes = max_input_bytes, max_context_bytes

    def _authorize(self, agent: str, version: str, binding: SourceBinding, scope: str) -> None:
        self._registry.require_tool(agent, version, binding.tool, scope=scope)
        if binding.resource_class in {"TRAINING", "HOLDOUT"}:
            self._registry.require_tool(
                agent, version, binding.resource_class + "_READ", scope=scope
            )

    async def compile(
        self, agent_id: str, version: str, *, task_id: str, scope: str
    ) -> CompiledContext:
        if scope not in {"PAPER", "SANDBOX"} or not 1 <= len(task_id) <= 128:
            raise ValueError("Invalid context scope or task ID")
        manifest = self._registry.get(agent_id, version)
        semaphore = asyncio.Semaphore(self._concurrency)

        async def collect(name: str) -> ContextInput:
            binding = self._sources.get(name)
            if binding is None:
                return ContextInput(input_name=name, status="MISSING")
            try:
                self._authorize(agent_id, version, binding, scope)
            except PermissionError:
                return ContextInput(input_name=name, status="DENIED")
            try:
                async with semaphore:
                    self._authorize(agent_id, version, binding, scope)
                    raw = await asyncio.wait_for(binding.fetch(), timeout=self._timeout)
                record = EvidenceRecord.model_validate_json(raw.model_dump_json())
            except PermissionError:
                return ContextInput(input_name=name, status="DENIED")
            except TimeoutError:
                return ContextInput(input_name=name, status="TIMEOUT")
            except Exception:
                return ContextInput(input_name=name, status="FAILED")
            return (
                ContextInput(
                    input_name=name,
                    status="AVAILABLE",
                    resource_class=binding.resource_class,
                    source_tool=binding.tool,
                    source_id=record.source_id,
                    as_of=record.as_of,
                    valid_until=record.valid_until,
                    content_ref=record.content_ref,
                    payload_json=record.payload_json,
                )
                if record.trusted
                else ContextInput(input_name=name, status="UNTRUSTED")
            )

        tasks = [asyncio.create_task(collect(name)) for name in manifest.required_inputs]
        try:
            done, pending = await asyncio.wait(tasks, timeout=self._overall)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            rows = [
                task.result() if task in done else ContextInput(input_name=name, status="TIMEOUT")
                for name, task in zip(manifest.required_inputs, tasks)
            ]
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        now = self._clock()
        _require_aware(now)
        now = now.astimezone(timezone.utc)
        for index, row in enumerate(rows):
            if row.status != "AVAILABLE":
                continue
            binding = self._sources[row.input_name]
            status = None
            try:
                self._authorize(agent_id, version, binding, scope)
            except PermissionError:
                status = "DENIED"
            if status is None:
                status = (
                    "FUTURE"
                    if row.as_of > now
                    else "STALE"
                    if row.valid_until <= now
                    else "BUDGET_EXCEEDED"
                    if len(row.payload_json.encode()) > self._input_bytes
                    else None
                )
            if status:
                rows[index] = ContextInput(input_name=row.input_name, status=status)

        def assemble() -> CompiledContext:
            available = [row for row in rows if row.status == "AVAILABLE"]
            context = CompiledContext(
                context_ref="",
                agent_id=agent_id,
                agent_version=version,
                manifest_ref=manifest.manifest_ref,
                task_id=task_id,
                scope=scope,
                status="READY" if len(available) == len(rows) else "DEGRADED",
                inputs=tuple(rows),
                input_refs=tuple(row.content_ref for row in available),
                created_at=now,
                valid_until=min(
                    [now + timedelta(seconds=5), *(row.valid_until for row in available)]
                ),
            )
            return context.model_copy(update={"context_ref": _context_ref(context)})

        context = assemble()
        for index in reversed(range(len(rows))):
            if len(context.model_dump_json().encode()) <= self._context_bytes:
                break
            if rows[index].status == "AVAILABLE":
                rows[index] = ContextInput(
                    input_name=rows[index].input_name, status="BUDGET_EXCEEDED"
                )
                context = assemble()
        if len(context.model_dump_json().encode()) > self._context_bytes:
            raise ValueError("Context metadata exceeds byte budget")
        require_current_context(context, now=now)
        return context
