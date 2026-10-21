import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import BaseModel, ConfigDict, create_model

from ai_quant_os.agent_context import ContextCompiler, EvidenceRecord, SourceBinding
from ai_quant_os.agent_registry import AgentToolBoundary, load_agent_catalog

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
    path, registry, handlers, clock=lambda: NOW, output_schema=RegimeDistribution
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
        tools=AgentToolBoundary(registry, {}),
        clock=clock,
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
    async def run():
        registry = load_agent_catalog()
        evidence = await context(registry)
        entered = asyncio.Event()
        calls = []

        async def handler(invocation):
            calls.append(1)
            entered.set()
            await asyncio.sleep(30)

        path = tmp_path / "runtime.sqlite3"
        async with await open_runtime(path, registry, handler) as runtime:
            task = asyncio.create_task(runtime.run("run-1", evidence))
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        async with await open_runtime(path, registry, handler) as runtime:
            result = await runtime.resume("run-1")
            assert result.status == "UNCERTAIN"
            assert result.output_json is None
        assert calls == [1]

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
