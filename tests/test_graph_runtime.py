import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import BaseModel, ConfigDict, create_model

from ai_quant_os.agent_context import ContextCompiler, EvidenceRecord, SourceBinding
from ai_quant_os.agent_registry import AgentToolBoundary, load_agent_catalog
from ai_quant_os.agent_model_router import AgentModelRouter, LlmResult
from ai_quant_os.cost_governor import ModelTier
from ai_quant_os.paper_telemetry import PaperTelemetry

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class RegimeDistribution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    label: str


async def context(registry):
    async def fetch():
        return EvidenceRecord(
            source_id="fixture",
            as_of=NOW,
            valid_until=NOW + timedelta(seconds=4),
            trusted=True,
            payload_json='{"value":1}',
        )

    return await ContextCompiler(
        registry,
        {
            key: SourceBinding(tool="WORLD_STATE_READ", resource_class="GENERAL", fetch=fetch)
            for key in registry.get("A02", "0.1.0").required_inputs
        },
        clock=lambda: NOW,
    ).compile("A02", "0.1.0", task_id="task", scope="PAPER")


async def open_runtime(
    path,
    registry,
    handlers,
    clock=lambda: NOW,
    output_schema=RegimeDistribution,
    tool_handlers=None,
    **runtime_options,
):
    # Import inside the test so missing implementation is an explicit failing assertion.
    import importlib.util

    assert importlib.util.find_spec("ai_quant_os.graph_runtime") is not None
    from ai_quant_os.graph_runtime import AgentImplementation, GraphRuntime

    implementations = (
        {
            ("A02", "0.1.0"): AgentImplementation(
                manifest_ref=registry.get("A02", "0.1.0").manifest_ref,
                output_schema=output_schema,
                invoke=handlers,
            )
        }
        if handlers
        else {}
    )
    return GraphRuntime.open(
        path,
        registry=registry,
        implementations=implementations,
        tools=AgentToolBoundary(registry, tool_handlers or {}),
        clock=clock,
        **runtime_options,
    )


def test_runtime_rejects_output_schemas_that_ignore_unknown_fields(tmp_path):
    PermissiveOutput = create_model("RegimeDistribution", label=(str, ...))

    async def run():
        registry = load_agent_catalog()

        async def handler(invocation):
            return PermissiveOutput(label="TRENDING")

        with pytest.raises(ValueError, match="Agent implementation contract mismatch"):
            async with await open_runtime(
                tmp_path / "runtime.sqlite3",
                registry,
                handler,
                output_schema=PermissiveOutput,
            ):
                pass

    asyncio.run(run())


def test_real_graph_executes_once_and_survives_restart(tmp_path):
    async def run():
        registry = load_agent_catalog()
        evidence = await context(registry)
        calls = []

        async def handler(invocation):
            calls.append(invocation.context.context_ref)
            return RegimeDistribution(label="TRENDING")

        path = tmp_path / "runtime.sqlite3"
        async with await open_runtime(path, registry, handler) as runtime:
            result = await runtime.run("run-1", evidence)
            assert result.status == "COMPLETED"
            assert result.output_json == '{"label":"TRENDING"}'
            assert result.context_ref == evidence.context_ref
            assert result.requires_escalation is True
            observations = runtime.quality_receipts()
            assert tuple(row.run_id for row in observations) == ("run-1",)
            assert observations[0].status == "COMPLETED"
            quality_receipts, quality_traces = runtime.quality_inventory()
            assert quality_receipts == observations
            assert tuple(row.run_id for row in quality_traces) == ("run-1",)
            assert quality_traces[0].status == "COMPLETED"
            assert quality_traces[0].timing_state == "AVAILABLE"
            assert quality_traces[0].elapsed_seconds is not None
            assert runtime.graph.get_graph().nodes.keys() >= {"admit", "execute", "finalize"}
        async with await open_runtime(path, registry, handler) as runtime:
            assert await runtime.resume("run-1") == result
            assert await runtime.run("run-1", evidence) == result
            assert runtime.quality_receipts() == observations
        assert calls == [evidence.context_ref]

    asyncio.run(run())


def test_graph_and_tool_spans_keep_only_bounded_metadata_and_parentage(tmp_path):
    pytest.importorskip("opentelemetry.sdk.trace.export.in_memory_span_exporter")
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    async def run():
        registry = load_agent_catalog()
        evidence = await context(registry)
        exporter = InMemorySpanExporter()
        telemetry = PaperTelemetry(exporter=exporter)
        tool_calls = []

        async def read_tool(invocation):
            tool_calls.append(invocation.tool)
            return {"private_payload": "must-not-be-an-attribute"}

        async def handler(invocation):
            await invocation.tools.call(
                "WORLD_STATE_READ",
                {"instrument_id": "EURUSD", "private_input": "must-not-be-an-attribute"},
            )
            return RegimeDistribution(label="TRENDING")

        try:
            async with await open_runtime(
                tmp_path / "traced.sqlite3",
                registry,
                handler,
                telemetry=telemetry,
                tool_handlers={"WORLD_STATE_READ": read_tool},
            ) as runtime:
                with telemetry.span("paper.http", root=True):
                    receipt = await runtime.run("trace-run", evidence)
                assert receipt.status == "COMPLETED"
                assert tool_calls == ["WORLD_STATE_READ"]
                assert telemetry.flush()
                assert (
                    'ai_quant_paper_agent_executions_total{agent_id="A02",status="COMPLETED"} 1\n'
                    in telemetry.metrics_text()
                )

            spans = exporter.get_finished_spans()
            by_name = {span.name: span for span in spans}
            assert set(by_name) == {
                "paper.http",
                "paper.agent.graph.admit",
                "paper.agent.graph.execute",
                "paper.agent.invoke",
                "paper.agent.tool.call",
                "paper.agent.source.read",
                "paper.agent.graph.finalize",
            }
            root = by_name["paper.http"]
            execute = by_name["paper.agent.graph.execute"]
            invoke = by_name["paper.agent.invoke"]
            tool = by_name["paper.agent.tool.call"]
            source = by_name["paper.agent.source.read"]
            assert execute.parent.span_id == root.context.span_id
            assert invoke.parent.span_id == execute.context.span_id
            assert tool.parent.span_id == invoke.context.span_id
            assert source.parent.span_id == tool.context.span_id
            assert all(span.context.trace_id == root.context.trace_id for span in spans)
            assert execute.attributes["ai_quant.agent.phase"] == "EXECUTE"
            assert invoke.attributes["ai_quant.agent.id"] == "A02"
            assert invoke.attributes["ai_quant.agent.version"] == "0.1.0"
            assert tool.attributes["ai_quant.agent.tool"] == "WORLD_STATE_READ"
            assert source.attributes["ai_quant.agent.phase"] == "SOURCE"
            assert all(
                "private" not in str(value) for span in spans for value in span.attributes.values()
            )
            assert all(
                key
                in {
                    "ai_quant.mode",
                    "ai_quant.agent.id",
                    "ai_quant.agent.version",
                    "ai_quant.agent.phase",
                    "ai_quant.agent.tool",
                }
                for span in spans
                for key in span.attributes
            )
        finally:
            telemetry.close()

    asyncio.run(run())


def test_graph_model_route_is_typed_durable_and_injected_only_for_allowlisted_agent(tmp_path):
    async def run():
        registry = load_agent_catalog()
        evidence = await context(registry)
        calls = []

        async def deterministic(invocation):
            calls.append("deterministic")
            return RegimeDistribution(label="TRENDING")

        async def provider(task, tier, invocation):
            calls.append("provider")
            assert task.agent_id == "A02"
            assert task.task_id == "task"
            assert tier is ModelTier.ROUTINE
            assert invocation.context.context_ref == evidence.context_ref
            return LlmResult(RegimeDistribution(label="RANGING"), input_tokens=17, output_tokens=3)

        router = AgentModelRouter()
        router.register_contextual_provider(
            "fixture-provider", tiers={ModelTier.ROUTINE}, invoke=provider
        )
        path = tmp_path / "runtime.sqlite3"
        async with await open_runtime(
            path,
            registry,
            deterministic,
            model_router=router,
            model_routing={("A02", "0.1.0"): (100, 50)},
        ) as runtime:
            receipt = await runtime.run("model-run", evidence)
            assert receipt.status == "COMPLETED"
            assert receipt.output_json == '{"label":"RANGING"}'
            trace = runtime.trace("model-run")
            assert trace.schema_version == "1.1"
            assert trace.routing.execution_path == "PROVIDER"
            assert trace.routing.provider_id == "fixture-provider"
            assert trace.routing.attempted_provider_id == "fixture-provider"
            assert trace.routing.routed_tier == "ROUTINE"
            assert trace.routing.input_tokens == 17
            assert trace.routing.output_tokens == 3
        async with await open_runtime(path, registry, deterministic) as runtime:
            durable_trace = runtime.trace("model-run")
            assert durable_trace.model_dump_json() == trace.model_dump_json()
            assert durable_trace.events[0].status == "CREATED"
            assert durable_trace.events[1].status == "RUNNING"
            assert durable_trace.events[2].status == "COMPLETED"

        async def unavailable_provider(task, tier, invocation):
            raise RuntimeError("provider credential must not escape into the receipt")

        failing_router = AgentModelRouter()
        failing_router.register_contextual_provider(
            "failing-provider", tiers={ModelTier.ROUTINE}, invoke=unavailable_provider
        )
        async with await open_runtime(
            path,
            registry,
            deterministic,
            model_router=failing_router,
            model_routing={("A02", "0.1.0"): (100, 50)},
        ) as runtime:
            fallback = await runtime.run("fallback-run", evidence)
            assert fallback.status == "COMPLETED"
            assert fallback.output_json == '{"label":"TRENDING"}'
            fallback_trace = runtime.trace("fallback-run")
            assert fallback_trace.routing.execution_path == "DETERMINISTIC"
            assert fallback_trace.routing.provider_id is None
            assert fallback_trace.routing.attempted_provider_id == "failing-provider"
            assert fallback_trace.routing.fallback_reasons == ("PROVIDER_FAILED",)
            assert "credential" not in fallback_trace.model_dump_json().lower()

        assert calls == ["provider", "deterministic"]

    asyncio.run(run())


@pytest.mark.parametrize(
    "input_tokens,output_tokens",
    [(True, 1), (1.5, 1), (10_000_001, 1), (1, False), (1, 1.5), (1, 10_000_001)],
)
def test_invalid_provider_token_usage_falls_back_without_stranding_graph_run(
    tmp_path, input_tokens, output_tokens
):
    async def run():
        registry = load_agent_catalog()
        evidence = await context(registry)
        calls = []

        async def deterministic(invocation):
            calls.append("deterministic")
            return RegimeDistribution(label="TRENDING")

        async def malformed_provider(task, tier, invocation):
            return LlmResult(
                RegimeDistribution(label="RANGING"),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

        router = AgentModelRouter()
        router.register_contextual_provider(
            "malformed-provider", tiers={ModelTier.ROUTINE}, invoke=malformed_provider
        )
        async with await open_runtime(
            tmp_path / "runtime.sqlite3",
            registry,
            deterministic,
            model_router=router,
            model_routing={("A02", "0.1.0"): (100, 50)},
        ) as runtime:
            receipt = await runtime.run("malformed-usage", evidence)
            trace = runtime.trace("malformed-usage")
            assert receipt.status == "COMPLETED"
            assert receipt.output_json == '{"label":"TRENDING"}'
            assert trace.routing.execution_path == "DETERMINISTIC"
            assert trace.routing.attempted_provider_id == "malformed-provider"
            assert trace.routing.fallback_reasons == ("PROVIDER_FAILED",)
            assert trace.routing.input_tokens is None
            assert trace.routing.output_tokens is None
        assert calls == ["deterministic"]

    asyncio.run(run())


@pytest.mark.parametrize(
    "kind, expected",
    [
        ("unavailable", "UNAVAILABLE"),
        ("expired", "EXPIRED"),
        ("quarantine", "DENIED"),
        ("degraded", "WAIT_FOR_DATA"),
        ("wrong_output", "INVALID"),
        ("failed", "FAILED"),
        ("timeout", "TIMEOUT"),
        ("expires_during_call", "EXPIRED"),
        ("quarantine_during_call", "DENIED"),
    ],
)
def test_failures_withhold_output_and_never_repeat(tmp_path, kind, expected):
    async def run():
        registry = load_agent_catalog()
        evidence = await context(registry)
        time = [NOW]
        calls = []
        if kind == "expired":
            time[0] = evidence.valid_until
        if kind == "quarantine":
            registry.set_lifecycle("A02", "0.1.0", "QUARANTINED")
        if kind == "degraded":
            evidence = await ContextCompiler(registry, {}, clock=lambda: NOW).compile(
                "A02", "0.1.0", task_id="task", scope="PAPER"
            )

        async def handler(invocation):
            calls.append(1)
            if kind == "wrong_output":
                return {"label": "TRENDING"}
            if kind == "failed":
                raise RuntimeError("private-provider-secret")
            if kind == "timeout":
                await asyncio.sleep(30)
            if kind == "expires_during_call":
                time[0] = evidence.valid_until
            if kind == "quarantine_during_call":
                registry.set_lifecycle("A02", "0.1.0", "QUARANTINED")
            return RegimeDistribution(label="TRENDING")

        async with await open_runtime(
            tmp_path / "runtime.sqlite3",
            registry,
            None if kind == "unavailable" else handler,
            clock=lambda: time[0],
        ) as runtime:
            result = await runtime.run("run-1", evidence)
            assert result.status == expected
            assert result.output_json is None
            assert "private-provider-secret" not in result.model_dump_json()
            assert await runtime.resume("run-1") == result
        assert len(calls) == (
            0 if kind in {"unavailable", "expired", "quarantine", "degraded"} else 1
        )

    asyncio.run(run())


def test_cancelled_invocation_is_uncertain_after_restart_not_reexecuted(tmp_path):
    pytest.importorskip("opentelemetry.sdk.trace.export.in_memory_span_exporter")
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    async def run():
        registry = load_agent_catalog()
        evidence = await context(registry)
        telemetry = PaperTelemetry(exporter=InMemorySpanExporter())
        entered = asyncio.Event()
        calls = []

        async def handler(invocation):
            calls.append(1)
            entered.set()
            await asyncio.sleep(30)

        path = tmp_path / "runtime.sqlite3"
        try:
            async with await open_runtime(path, registry, handler, telemetry=telemetry) as runtime:
                task = asyncio.create_task(runtime.run("run-1", evidence))
                await entered.wait()
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            async with await open_runtime(path, registry, handler, telemetry=telemetry) as runtime:
                result = await runtime.resume("run-1")
                assert result.status == "UNCERTAIN"
                assert result.output_json is None
            metrics = telemetry.metrics_text()
            labels = 'agent_id="A02",status="UNCERTAIN"'
            assert f"ai_quant_paper_agent_executions_total{{{labels}}} 1\n" in metrics
            assert (
                f"ai_quant_paper_agent_execution_duration_seconds_count{{{labels}}}" not in metrics
            )
            assert calls == [1]
        finally:
            telemetry.close()

    asyncio.run(run())


def test_checkpointed_admission_resumes_with_current_permissions(tmp_path):
    async def run():
        registry = load_agent_catalog()
        evidence = await context(registry)
        calls = []

        async def handler(invocation):
            calls.append(1)
            return RegimeDistribution(label="TRENDING")

        path = tmp_path / "runtime.sqlite3"
        async with await open_runtime(path, registry, handler) as runtime:
            await runtime.run("run-1", evidence, pause_before_execution=True)
            state = await runtime.graph.aget_state(runtime.config("run-1"))
            assert state.next == ("execute",)
        registry.set_lifecycle("A02", "0.1.0", "QUARANTINED")
        async with await open_runtime(path, registry, handler) as runtime:
            result = await runtime.resume("run-1")
            assert result.status == "DENIED"
        assert calls == []

    asyncio.run(run())


def test_run_identity_cannot_be_rebound(tmp_path):
    async def run():
        registry = load_agent_catalog()
        evidence = await context(registry)
        async with await open_runtime(tmp_path / "runtime.sqlite3", registry, None) as runtime:
            await runtime.run("run-1", evidence)
            changed = await ContextCompiler(registry, {}, clock=lambda: NOW).compile(
                "A02", "0.1.0", task_id="other", scope="PAPER"
            )
            with pytest.raises(ValueError, match="conflict"):
                await runtime.run("run-1", changed)

    asyncio.run(run())


def test_rehashed_record_edit_is_rejected_by_audit_binding(tmp_path):
    import json
    import sqlite3
    from hashlib import sha256

    async def run():
        registry = load_agent_catalog()
        evidence = await context(registry)
        path = tmp_path / "runtime.sqlite3"
        async with await open_runtime(path, registry, None) as runtime:
            await runtime.run("run-1", evidence)
            with sqlite3.connect(path) as db:
                raw = db.execute("SELECT record_json FROM graph_runs").fetchone()[0]
                changed = json.loads(raw)
                changed["receipt"]["status"] = "FAILED"
                from ai_quant_os.graph_ledger import GraphRecord

                raw = GraphRecord.model_validate(changed).model_dump_json()
                db.execute(
                    "UPDATE graph_runs SET record_json=?, record_hash=?",
                    (raw, sha256(raw.encode()).hexdigest()),
                )
            with pytest.raises(ValueError, match="audit binding"):
                runtime.receipt("run-1")
            with pytest.raises(ValueError, match="audit binding"):
                await runtime.resume("run-1")

    asyncio.run(run())


def test_changed_graph_thread_identity_cannot_execute_another_run(tmp_path):
    async def run():
        registry = load_agent_catalog()
        evidence = await context(registry)
        calls = []

        async def handler(invocation):
            calls.append(1)
            return RegimeDistribution(label="TRENDING")

        async with await open_runtime(tmp_path / "runtime.sqlite3", registry, handler) as runtime:
            await runtime.run("run-1", evidence, pause_before_execution=True)
            await runtime.graph.aupdate_state(runtime.config("run-1"), {"run_id": "run-2"})
            with pytest.raises(ValueError, match="checkpoint identity"):
                await runtime.resume("run-1")
        assert calls == []

    asyncio.run(run())


def test_unknown_resume_does_not_consume_runtime_capacity(tmp_path):
    async def run():
        registry = load_agent_catalog()
        async with await open_runtime(tmp_path / "runtime.sqlite3", registry, None) as runtime:
            with pytest.raises(KeyError):
                await runtime.resume("unknown")
            assert runtime._locks == {}

    asyncio.run(run())


def test_bound_sdk_rejects_capital_tools_inside_agent_execution(tmp_path):
    async def run():
        registry = load_agent_catalog()
        evidence = await context(registry)

        async def handler(invocation):
            await invocation.tools.call("BROKER_SUBMIT", {})
            raise AssertionError("Denied tool must not return")

        async with await open_runtime(tmp_path / "runtime.sqlite3", registry, handler) as runtime:
            result = await runtime.run("run-1", evidence)
            assert result.status == "DENIED"
            assert result.output_json is None

    asyncio.run(run())


def test_environment_tracing_is_disabled_inside_graph(tmp_path, monkeypatch):
    from langsmith.run_helpers import get_tracing_context

    monkeypatch.setenv("LANGSMITH_TRACING", "true")

    async def run():
        registry = load_agent_catalog()
        evidence = await context(registry)

        async def handler(invocation):
            assert get_tracing_context()["enabled"] is False
            return RegimeDistribution(label="TRENDING")

        async with await open_runtime(tmp_path / "runtime.sqlite3", registry, handler) as runtime:
            result = await runtime.run("run-1", evidence)
            assert result.status == "COMPLETED"

    asyncio.run(run())


def test_ledger_cannot_publish_completion_without_invocation_reservation(tmp_path):
    from ai_quant_os.context_checkpoints import ContextCheckpoint
    from ai_quant_os.graph_ledger import GraphLedger

    async def run():
        registry = load_agent_catalog()
        evidence = await context(registry)
        ledger = GraphLedger(tmp_path / "runtime.sqlite3")
        try:
            ledger.create(
                "run-1",
                ContextCheckpoint(
                    schema_version="1.0", context=evidence, manifest=registry.get("A02", "0.1.0")
                ),
            )
            with pytest.raises(ValueError, match="Invalid graph transition"):
                ledger.transition("run-1", "CREATED", "COMPLETED", '{"label":"TRENDING"}')
            assert ledger.get("run-1").receipt.status == "CREATED"
        finally:
            ledger.close()

    asyncio.run(run())
