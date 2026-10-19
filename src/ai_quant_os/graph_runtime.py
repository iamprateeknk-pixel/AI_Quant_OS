"""LangGraph advisory execution with durable reservations and current context admission."""

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, TypedDict

import aiosqlite
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langsmith.run_helpers import tracing_context
from pydantic import BaseModel

from ai_quant_os.agent_context import CompiledContext, require_context_for_agent
from ai_quant_os.agent_envelope import AgentEnvelope
from ai_quant_os.agent_registry import AgentRegistry, AgentToolBoundary
from ai_quant_os.context_checkpoints import ContextCheckpoint
from ai_quant_os.graph_ledger import GraphLedger, GraphReceipt, GraphRecord


class GraphState(TypedDict):
    run_id: str


@dataclass(frozen=True)
class AgentImplementation:
    manifest_ref: str
    output_schema: type[BaseModel]
    invoke: Callable[["AgentInvocation"], Awaitable[BaseModel]]


@dataclass(frozen=True)
class AgentInvocation:
    context: CompiledContext
    tools: "BoundAgentTools"


class BoundAgentTools:
    def __init__(self, context: CompiledContext, runtime: "GraphRuntime") -> None:
        self._context, self._runtime = context, runtime

    async def call(self, tool: str, payload: dict[str, object]) -> dict[str, object]:
        self._runtime.require_context(self._context)
        result = await self._runtime._tools.call(
            self._context.agent_id,
            self._context.agent_version,
            tool,
            scope=self._context.scope,
            payload=payload,
            context_ref=self._context.context_ref,
            task_id=self._context.task_id,
        )
        self._runtime.require_context(self._context)
        return result


class GraphRuntime:
    def __init__(
        self,
        ledger: GraphLedger,
        saver: AsyncSqliteSaver,
        *,
        registry: AgentRegistry,
        implementations: dict[tuple[str, str], AgentImplementation],
        tools: AgentToolBoundary,
        clock: Callable[[], datetime],
    ) -> None:
        self._ledger, self._registry, self._tools, self._clock = ledger, registry, tools, clock
        self._implementations = implementations.copy()
        for (agent, version), implementation in self._implementations.items():
            manifest = registry.get(agent, version)
            if (
                implementation.manifest_ref != manifest.manifest_ref
                or not issubclass(implementation.output_schema, BaseModel)
                or implementation.output_schema.model_config.get("extra") != "forbid"
                or implementation.output_schema.__name__ != manifest.output_type
                or not callable(implementation.invoke)
            ):
                raise ValueError("Agent implementation contract mismatch")
        self._locks: dict[str, asyncio.Lock] = {}
        builder = StateGraph(GraphState)
        builder.add_node("admit", self._admit)
        builder.add_node("execute", self._execute)
        builder.add_node("finalize", self._finalize)
        builder.add_edge(START, "admit")
        builder.add_edge("admit", "execute")
        builder.add_edge("execute", "finalize")
        builder.add_edge("finalize", END)
        self.graph = builder.compile(checkpointer=saver, name="advisory-supervisor-v0.1.0")

    @classmethod
    @asynccontextmanager
    async def open(
        cls,
        path: Path,
        *,
        registry: AgentRegistry,
        implementations: dict[tuple[str, str], AgentImplementation],
        tools: AgentToolBoundary,
        clock: Callable[[], datetime] | None = None,
    ):
        ledger = GraphLedger(path, clock=clock)
        try:
            async with aiosqlite.connect(str(path)) as connection:
                saver = AsyncSqliteSaver(
                    connection,
                    serde=JsonPlusSerializer(
                        pickle_fallback=False,
                        allowed_json_modules=[],
                        allowed_msgpack_modules=[],
                    ),
                )
                await saver.setup()
                yield cls(
                    ledger,
                    saver,
                    registry=registry,
                    implementations=implementations,
                    tools=tools,
                    clock=clock or (lambda: datetime.now(timezone.utc)),
                )
        finally:
            ledger.close()

    @staticmethod
    def config(run_id: str) -> dict:
        # No environment-derived callbacks or remote LangSmith traces.
        return {
            "configurable": {"thread_id": run_id},
            "recursion_limit": 8,
            "callbacks": [],
            "metadata": {"graph_version": "0.1.0"},
        }

    def require_context(self, context: CompiledContext) -> None:
        require_context_for_agent(
            context,
            registry=self._registry,
            agent_id=context.agent_id,
            agent_version=context.agent_version,
            task_id=context.task_id,
            scope=context.scope,
            now=self._clock(),
        )

    def _admission(self, record: GraphRecord) -> str | None:
        try:
            self.require_context(record.checkpoint.context)
        except PermissionError:
            return "DENIED"
        except ValueError:
            return "EXPIRED"
        if record.checkpoint.context.status != "READY":
            return "WAIT_FOR_DATA"
        if (record.receipt.agent_id, record.receipt.agent_version) not in self._implementations:
            return "UNAVAILABLE"
        return None

    async def _admit(self, state: GraphState) -> dict:
        record = self._ledger.get(state["run_id"])
        if record.receipt.status == "CREATED":
            failure = self._admission(record)
            if failure:
                self._ledger.transition(state["run_id"], "CREATED", failure)
        return {}

    async def _execute(self, state: GraphState) -> dict:
        run_id = state["run_id"]
        record = self._ledger.get(run_id)
        if record.receipt.status != "CREATED":
            return {}
        failure = self._admission(record)
        if failure:
            self._ledger.transition(run_id, "CREATED", failure)
            return {}
        context = record.checkpoint.context
        implementation = self._implementations[(context.agent_id, context.agent_version)]
        # Commit before the external invocation; never replay a RUNNING reservation.
        self._ledger.transition(run_id, "CREATED", "RUNNING")
        status, raw = "FAILED", None
        remaining = (context.valid_until - self._clock()).total_seconds()
        timeout = min(record.checkpoint.manifest.timeout_seconds, remaining)
        try:
            self.require_context(context)
            if timeout <= 0:
                raise ValueError("Expired context")
            output = await asyncio.wait_for(
                implementation.invoke(AgentInvocation(context, BoundAgentTools(context, self))),
                timeout=timeout,
            )
            self.require_context(context)
            if not isinstance(output, implementation.output_schema):
                status = "INVALID"
            else:
                validated = implementation.output_schema.model_validate_json(
                    output.model_dump_json()
                )
                if isinstance(validated, AgentEnvelope):
                    validated.require_context(context)
                raw = json.dumps(
                    validated.model_dump(mode="json"),
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                status = "COMPLETED" if len(raw.encode()) <= 65536 else "INVALID"
        except TimeoutError:
            status = "TIMEOUT"
        except PermissionError:
            status = "DENIED"
        except ValueError:
            status = "EXPIRED" if self._clock() >= context.valid_until else "INVALID"
        except Exception:
            status = "FAILED"
        # Caller cancellation deliberately leaves RUNNING: outcome may be uncertain.
        self._ledger.transition(run_id, "RUNNING", status, raw if status == "COMPLETED" else None)
        return {}

    async def _finalize(self, state: GraphState) -> dict:
        self._ledger.get(state["run_id"])
        return {}

    async def run(
        self, run_id: str, context: CompiledContext, *, pause_before_execution: bool = False
    ) -> GraphReceipt:
        checkpoint = ContextCheckpoint(
            schema_version="1.0",
            context=context,
            manifest=self._registry.get(context.agent_id, context.agent_version),
        )
        self._ledger.create(run_id, checkpoint)
        return await self.resume(run_id, pause_before_execution=pause_before_execution)

    async def resume(self, run_id: str, *, pause_before_execution: bool = False) -> GraphReceipt:
        # One active invocation per thread in this process. The SQL reservation also
        # prevents duplicate calls across processes; ambiguous RUNNING is withheld.
        self._ledger.get(run_id)
        if run_id not in self._locks:
            if len(self._locks) >= 10000:
                raise ValueError("Graph runtime capacity exhausted")
            self._locks[run_id] = asyncio.Lock()
        async with self._locks[run_id]:
            record = self._ledger.get(run_id)
            if record.receipt.status == "RUNNING":
                return self._ledger.transition(run_id, "RUNNING", "UNCERTAIN").receipt
            if record.receipt.status != "CREATED":
                return record.receipt
            config = self.config(run_id)
            with tracing_context(enabled=False):
                state = await self.graph.aget_state(config)
                if state.values and state.values != {"run_id": run_id}:
                    raise ValueError("Graph checkpoint identity mismatch")
                await self.graph.ainvoke(
                    None if state.values else {"run_id": run_id},
                    config,
                    interrupt_before=["execute"] if pause_before_execution else [],
                    durability="sync",
                )
            return self._ledger.get(run_id).receipt

    def health_receipts(self, after_run_id: str = "") -> tuple[GraphReceipt, ...]:
        return self._ledger.health_receipts(after_run_id)

    def quality_receipts(self) -> tuple[GraphReceipt, ...]:
        """Read the verified durable receipt inventory for operational quality metrics."""
        return self._ledger.quality_receipts()

    def receipt(self, run_id: str) -> GraphReceipt:
        """Read archival state without invoking or changing the graph."""
        return self._ledger.get(run_id).receipt

    def completed_output(self, run_id: str) -> tuple[GraphReceipt, BaseModel]:
        """Read a current completed output after revalidating its pinned context."""
        record = self._ledger.get(run_id)
        receipt, context = record.receipt, record.checkpoint.context
        if receipt.status != "COMPLETED" or receipt.output_json is None:
            raise ValueError("Completed graph output is unavailable")
        self.require_context(context)
        implementation = self._implementations.get((context.agent_id, context.agent_version))
        if implementation is None or implementation.output_schema.__name__ != receipt.output_type:
            raise ValueError("Completed graph output implementation is unavailable")
        try:
            output = implementation.output_schema.model_validate_json(receipt.output_json)
            if isinstance(output, AgentEnvelope):
                output.require_context(context)
        except (TypeError, ValueError) as error:
            raise ValueError("Completed graph output failed current validation") from error
        return receipt, output

    def trace(self, run_id: str):
        return self._ledger.trace(run_id)

    def archive_page(self, *, after_run_id: str = "", head: str | None = None, limit: int = 25):
        return self._ledger.page(after_run_id=after_run_id, head=head, limit=limit)

    def current(self, run_id: str) -> bool:
        """Current evidence/permission eligibility; never establishes order authority."""
        record = self._ledger.get(run_id)
        try:
            self.require_context(record.checkpoint.context)
        except (ValueError, PermissionError):
            return False
        return True
