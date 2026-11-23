"""LangGraph advisory execution with durable reservations and current context admission."""

import asyncio
import json
from contextlib import asynccontextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic_ns
from typing import Awaitable, Callable, Mapping, TypedDict

import aiosqlite
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langsmith.run_helpers import tracing_context
from pydantic import BaseModel

from ai_quant_os.agent_context import CompiledContext, require_context_for_agent
from ai_quant_os.agent_envelope import AgentEnvelope
from ai_quant_os.agent_model_router import AgentModelRouter, RoutingDecision
from ai_quant_os.agent_permissions import READ_TOOLS
from ai_quant_os.agent_registry import AgentRegistry, AgentToolBoundary
from ai_quant_os.agent_routing import AgentExecutionRoute
from ai_quant_os.context_checkpoints import ContextCheckpoint
from ai_quant_os.graph_ledger import GraphLedger, GraphReceipt, GraphRecord
from ai_quant_os.graph_trace import GraphTrace, GraphTraceChild


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
        with self._runtime._span("paper.agent.tool.call") as span:
            self._runtime._tag_span(span, self._context, phase="TOOL", tool=tool)
            if tool in READ_TOOLS:
                with self._runtime._span("paper.agent.source.read") as source_span:
                    self._runtime._tag_span(source_span, self._context, phase="SOURCE", tool=tool)
                    result = await self._call_tool(tool, payload)
            else:
                result = await self._call_tool(tool, payload)
        self._runtime.require_context(self._context)
        return result

    async def _call_tool(self, tool: str, payload: dict[str, object]) -> dict[str, object]:
        return await self._runtime._tools.call(
            self._context.agent_id,
            self._context.agent_version,
            tool,
            scope=self._context.scope,
            payload=payload,
            context_ref=self._context.context_ref,
            task_id=self._context.task_id,
        )


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
        model_router: AgentModelRouter | None = None,
        model_routing: Mapping[tuple[str, str], tuple[int, int]] | None = None,
        telemetry=None,
    ) -> None:
        self._ledger, self._registry, self._tools, self._clock = ledger, registry, tools, clock
        self._implementations = implementations.copy()
        self._model_router = model_router if model_router is not None else AgentModelRouter()
        self._telemetry = telemetry
        self._model_routing = dict(model_routing or {})
        if set(self._model_routing) - set(self._implementations):
            raise ValueError("Model routing references an unavailable agent implementation")
        if any(
            type(estimates) is not tuple
            or len(estimates) != 2
            or any(type(value) is not int or value <= 0 for value in estimates)
            for estimates in self._model_routing.values()
        ):
            raise ValueError("Routed agents require positive input and output token estimates")
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
        builder.add_node(
            "admit",
            self._observed_node("paper.agent.graph.admit", "ADMIT", self._admit),
        )
        builder.add_node(
            "execute",
            self._observed_node("paper.agent.graph.execute", "EXECUTE", self._execute),
        )
        builder.add_node(
            "finalize",
            self._observed_node("paper.agent.graph.finalize", "FINALIZE", self._finalize),
        )
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
        model_router: AgentModelRouter | None = None,
        model_routing: Mapping[tuple[str, str], tuple[int, int]] | None = None,
        telemetry=None,
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
                    model_router=model_router,
                    model_routing=model_routing,
                    telemetry=telemetry,
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

    def _telemetry_enabled(self) -> bool:
        return self._telemetry is not None and self._telemetry.state().get("enabled") is True

    def _span(self, name: str):
        return self._telemetry.span(name) if self._telemetry_enabled() else nullcontext(None)

    @staticmethod
    def _tag_span(span, context: CompiledContext, *, phase: str, tool: str | None = None) -> None:
        if span is None:
            return
        span.set("ai_quant.agent.id", context.agent_id)
        span.set("ai_quant.agent.version", context.agent_version)
        span.set("ai_quant.agent.phase", phase)
        if tool is not None:
            span.set("ai_quant.agent.tool", tool)

    def _observed_node(self, name: str, phase: str, operation):
        async def observe(state: GraphState) -> dict:
            if not self._telemetry_enabled():
                return await operation(state)
            with self._span(name) as span:
                span.set("ai_quant.agent.phase", phase)
                return await operation(state)

        return observe

    def _record_agent(self, context: CompiledContext, status: str, started_ns: int | None) -> None:
        if self._telemetry is not None:
            try:
                self._telemetry.record_agent(
                    context.agent_id,
                    status,
                    monotonic_ns() - started_ns if started_ns is not None else None,
                )
            except Exception:
                pass

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
            started_ns = monotonic_ns()
            failure = self._admission(record)
            if failure:
                self._ledger.transition(state["run_id"], "CREATED", failure)
                self._record_agent(record.checkpoint.context, failure, started_ns)
        return {}

    async def _execute(self, state: GraphState) -> dict:
        run_id = state["run_id"]
        record = self._ledger.get(run_id)
        if record.receipt.status != "CREATED":
            return {}
        failure = self._admission(record)
        if failure:
            self._ledger.transition(run_id, "CREATED", failure)
            self._record_agent(record.checkpoint.context, failure, monotonic_ns())
            return {}
        context = record.checkpoint.context
        agent_key = (context.agent_id, context.agent_version)
        implementation = self._implementations[agent_key]
        invocation = AgentInvocation(context, BoundAgentTools(context, self))
        route_decision: RoutingDecision | None = None
        route_receipt: AgentExecutionRoute | None = None
        if agent_key in self._model_routing:
            estimated_input_tokens, estimated_output_tokens = self._model_routing[agent_key]
            route_decision = self._model_router.prepare(
                self._registry,
                context.agent_id,
                context.agent_version,
                task_id=context.task_id,
                estimated_input_tokens=estimated_input_tokens,
                estimated_output_tokens=estimated_output_tokens,
            )
            route_receipt = self._route_receipt(route_decision)
        # Commit before the external invocation; never replay a RUNNING reservation.
        self._ledger.transition(run_id, "CREATED", "RUNNING", routing=route_receipt)
        status, raw = "FAILED", None
        execution_started_ns = monotonic_ns()
        remaining = (context.valid_until - self._clock()).total_seconds()
        timeout = min(record.checkpoint.manifest.timeout_seconds, remaining)
        try:
            self.require_context(context)
            if timeout <= 0:
                raise ValueError("Expired context")
            if route_decision is None:
                with self._span("paper.agent.invoke") as span:
                    self._tag_span(span, context, phase="INVOKE")
                    output = await asyncio.wait_for(
                        implementation.invoke(invocation), timeout=timeout
                    )
            else:
                estimates = self._model_routing[agent_key]

                def record_route(decision: RoutingDecision) -> None:
                    nonlocal route_decision
                    route_decision = decision

                with self._span("paper.agent.invoke") as span:
                    self._tag_span(span, context, phase="INVOKE")
                    _, output = await asyncio.wait_for(
                        self._model_router.execute(
                            self._registry,
                            context.agent_id,
                            context.agent_version,
                            task_id=context.task_id,
                            deterministic=lambda: implementation.invoke(invocation),
                            estimated_input_tokens=estimates[0],
                            estimated_output_tokens=estimates[1],
                            provider_context=invocation,
                            decision=route_decision,
                            on_decision=record_route,
                        ),
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
        self._ledger.transition(
            run_id,
            "RUNNING",
            status,
            raw if status == "COMPLETED" else None,
            routing=self._route_receipt(route_decision) if route_decision is not None else None,
        )
        self._record_agent(context, status, execution_started_ns)
        return {}

    @staticmethod
    def _route_receipt(decision: RoutingDecision) -> AgentExecutionRoute:
        return AgentExecutionRoute(
            agent_id=decision.agent_id,
            agent_version=decision.agent_version,
            task_id=decision.task_id,
            squad=decision.squad,
            criticality=decision.criticality,
            kind=decision.kind,
            governor_decision=decision.governor_decision,
            routed_tier=decision.routed_tier.value,
            execution_path=decision.executed_via,
            provider_id=decision.provider_id,
            attempted_provider_id=decision.attempted_provider_id,
            fallback_reasons=decision.fallback_reasons,
            input_tokens=decision.input_tokens,
            output_tokens=decision.output_tokens,
        )

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
                receipt = self._ledger.transition(run_id, "RUNNING", "UNCERTAIN").receipt
                self._record_agent(record.checkpoint.context, "UNCERTAIN", None)
                return receipt
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

    def quality_inventory(self) -> tuple[tuple[GraphReceipt, ...], tuple[GraphTrace, ...]]:
        """Read bounded checked receipts and their matching audited execution traces."""
        receipts = self._ledger.quality_receipts()
        traces = tuple(self._ledger.trace(receipt.run_id) for receipt in receipts)
        return receipts, traces

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
        trace = self._ledger.trace(run_id)
        if trace.status != "COMPLETED":
            return trace
        record = self._ledger.get(run_id)
        implementation = self._implementations.get(
            (record.receipt.agent_id, record.receipt.agent_version)
        )
        if implementation is None or "calls" not in implementation.output_schema.model_fields:
            return trace
        if record.receipt.output_json is None:
            raise ValueError("Completed supervisor output is unavailable")
        output = implementation.output_schema.model_validate_json(record.receipt.output_json)
        if isinstance(output, AgentEnvelope):
            output.require_context(record.checkpoint.context)
        calls = getattr(output, "calls", None)
        if type(calls) is not tuple or len(calls) > 7:
            raise ValueError("Supervisor child calls exceed trace bounds")
        children = []
        for call in calls:
            if not all(
                type(value) is str
                for value in (call.run_id, call.agent_id, call.agent_version, call.status)
            ):
                raise ValueError("Supervisor child identity is invalid")
            try:
                child_trace = self._ledger.trace(call.run_id)
            except KeyError:
                if not call.status.startswith("NOT_STARTED"):
                    raise ValueError("Supervisor child receipt is missing") from None
                trace_available = False
            else:
                if call.status.startswith("NOT_STARTED") or (
                    call.run_id,
                    call.agent_id,
                    call.agent_version,
                    call.manifest_ref,
                    call.status,
                    call.context_ref,
                    call.output_sha256,
                ) != (
                    child_trace.run_id,
                    child_trace.agent_id,
                    child_trace.agent_version,
                    child_trace.manifest_ref,
                    child_trace.status,
                    child_trace.context_ref,
                    child_trace.output_sha256,
                ):
                    raise ValueError("Supervisor child receipt binding mismatch")
                if call.valid_until != child_trace.context_valid_until:
                    raise ValueError("Supervisor child expiry binding mismatch")
                trace_available = True
            children.append(
                GraphTraceChild(
                    run_id=call.run_id,
                    agent_id=call.agent_id,
                    agent_version=call.agent_version,
                    status=call.status,
                    trace_available=trace_available,
                )
            )
        payload = trace.model_dump(mode="python")
        payload.update(schema_version="1.2", children=tuple(children))
        return GraphTrace.model_validate(payload)

    def archive_page(
        self,
        *,
        after_run_id: str = "",
        head: str | None = None,
        limit: int = 25,
        agent_id: str | None = None,
        agent_version: str | None = None,
    ):
        return self._ledger.page(
            after_run_id=after_run_id,
            head=head,
            limit=limit,
            agent_id=agent_id,
            agent_version=agent_version,
        )

    def current(self, run_id: str) -> bool:
        """Current evidence/permission eligibility; never establishes order authority."""
        record = self._ledger.get(run_id)
        try:
            self.require_context(record.checkpoint.context)
        except (ValueError, PermissionError):
            return False
        return True
