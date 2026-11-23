import asyncio
import json
import sqlite3
from datetime import timedelta

import pytest

from fastapi.testclient import TestClient

from ai_quant_os.api import create_app
from ai_quant_os.agent_registry import load_agent_catalog
from test_graph_runtime import NOW, context, open_runtime, RegimeDistribution
from test_supervisor_api import NOW as API_NOW, HEADERS
from test_task_supervisor import task_run


def test_execution_times_are_observed_at_transitions_and_retained_on_restart(tmp_path):
    async def run():
        registry = load_agent_catalog()
        compiled = await context(registry)
        moment = [NOW + timedelta(seconds=1)]

        async def handler(invocation):
            moment[0] += timedelta(milliseconds=250)
            return RegimeDistribution(label="TRENDING")

        path = tmp_path / "graphs.sqlite3"
        async with await open_runtime(path, registry, handler, clock=lambda: moment[0]) as runtime:
            assert (await runtime.run("trace-test", compiled)).status == "COMPLETED"
            trace = runtime.trace("trace-test")
            assert trace.timing_state == "AVAILABLE"
            assert [row.status for row in trace.events] == ["CREATED", "RUNNING", "COMPLETED"]
            assert trace.events[0].observed_at == NOW + timedelta(seconds=1)
            assert trace.events[-1].observed_at == moment[0]
            assert str(trace.elapsed_seconds) == "0.25"
            assert trace.context_observed_at == NOW
        async with await open_runtime(path, registry, handler, clock=lambda: moment[0]) as runtime:
            assert runtime.trace("trace-test") == trace
            await runtime.resume("trace-test")
            assert runtime.trace("trace-test") == trace

    asyncio.run(run())


def test_protected_trace_api_exposes_bound_metadata_without_context_payloads(tmp_path):
    args = dict(
        operator_token="fixture-supervisor",
        clock=lambda: API_NOW,
        paper_db_path=tmp_path / "paper.sqlite3",
    )
    with TestClient(create_app(**args)) as client:
        _, receipt, plan = task_run(client, "DATA_REVIEW")
        url = "/api/v1/agents/graph-runs/task-review/trace"
        assert client.get(url).status_code == 401
        response = client.get(url, headers=HEADERS)
        assert response.status_code == 200, response.text
        trace = response.json()
        assert trace["run_id"] == "task-review"
        assert trace["scope"] == "PAPER"
        assert trace["context_ref"] == receipt["context_ref"]
        assert trace["output_trace_id"] == plan["trace_id"]
        assert trace["timing_state"] == "AVAILABLE"
        assert "output_json" not in trace and "checkpoint" not in trace
        assert (
            client.get("/api/v1/agents/graph-runs/missing/trace", headers=HEADERS).status_code
            == 404
        )
    with TestClient(create_app(**args)) as client:
        assert client.get(url, headers=HEADERS).json() == trace


def test_parent_trace_links_only_verified_saved_child_runs(tmp_path):
    with TestClient(
        create_app(
            operator_token="fixture-supervisor",
            clock=lambda: API_NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        _, _, plan = task_run(client, "DATA_REVIEW")
        child = plan["calls"][0]
        assert child["status"] == "COMPLETED"

        response = client.get("/api/v1/agents/graph-runs/task-review/trace", headers=HEADERS)
        assert response.status_code == 200
        trace = response.json()
        assert trace["schema_version"] == "1.2"
        assert trace["children"] == [
            {
                "run_id": child["run_id"],
                "agent_id": child["agent_id"],
                "agent_version": child["agent_version"],
                "status": child["status"],
                "trace_available": True,
            }
        ]
        assert "output_json" not in response.text
        child_trace = client.get(
            f"/api/v1/agents/graph-runs/{child['run_id']}/trace", headers=HEADERS
        )
        assert child_trace.status_code == 200


def test_protected_supervisor_api_exports_nested_graph_spans(tmp_path):
    pytest.importorskip("opentelemetry.sdk.trace.export.in_memory_span_exporter")
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from ai_quant_os.paper_telemetry import PaperTelemetry

    exporter = InMemorySpanExporter()
    telemetry = PaperTelemetry(exporter=exporter)
    try:
        with TestClient(
            create_app(
                operator_token="fixture-supervisor",
                clock=lambda: API_NOW,
                paper_db_path=tmp_path / "paper.sqlite3",
                telemetry=telemetry,
            )
        ) as client:
            context = client.post(
                "/api/v1/paper/tasks/supervise/context",
                headers=HEADERS,
                json={"task_id": "observed-task", "task_kind": "DATA_REVIEW"},
            )
            assert context.status_code == 200
            response = client.post(
                "/api/v1/paper/agents/execute",
                headers=HEADERS,
                json={
                    "run_id": "observed-task",
                    "context_ref": context.json()["context_ref"],
                },
            )
            assert response.status_code == 200
            observed_trace = int(response.headers["X-Paper-Trace-Id"], 16)
        assert telemetry.flush()
        spans = [
            span
            for span in exporter.get_finished_spans()
            if span.context.trace_id == observed_trace
        ]
        names = [span.name for span in spans]
        assert "paper.http" in names
        assert names.count("paper.agent.graph.execute") >= 2
        assert "paper.agent.tool.call" in names
        by_span_id = {span.context.span_id: span for span in spans}
        graph_admits = [span for span in spans if span.name == "paper.agent.graph.admit"]
        tool_span_ids = {
            span.context.span_id for span in spans if span.name == "paper.agent.tool.call"
        }
        assert any(
            span.parent.span_id in by_span_id
            and by_span_id[span.parent.span_id].name == "paper.http"
            for span in graph_admits
        )
        assert any(span.parent.span_id in tool_span_ids for span in graph_admits)
        graph_spans = [span for span in spans if span.name.startswith("paper.agent")]
        assert all(
            key
            in {
                "ai_quant.mode",
                "ai_quant.agent.id",
                "ai_quant.agent.version",
                "ai_quant.agent.phase",
                "ai_quant.agent.tool",
            }
            for span in graph_spans
            for key in span.attributes
        )
        assert all(
            "observed-task" not in str(value)
            for span in graph_spans
            for value in span.attributes.values()
        )
    finally:
        telemetry.close()


def rewrite_events(path, mutate):
    """Model an imported legacy journal or attacker able to rehash its local chain."""
    from ai_quant_os.audit_chain import event_hash, GENESIS

    with sqlite3.connect(path) as db:
        rows = db.execute(
            "SELECT sequence,c.event_key,event_json FROM audit_chain c JOIN audit_events e ON c.event_key=e.event_key ORDER BY sequence"
        ).fetchall()
        previous = GENESIS
        for sequence, key, raw in rows:
            event = json.loads(raw)
            mutate(event)
            raw = json.dumps(event, sort_keys=True)
            db.execute("UPDATE audit_events SET event_json=? WHERE event_key=?", (raw, key))
            digest = event_hash(sequence, key, raw, previous)
            db.execute(
                "UPDATE audit_chain SET previous_hash=?,event_hash=? WHERE sequence=?",
                (previous, digest, sequence),
            )
            previous = digest
        db.execute("UPDATE audit_head SET head_hash=? WHERE singleton=1", (previous,))


@pytest.mark.parametrize("kind", ["legacy", "partial", "regression"])
def test_missing_and_regressing_observations_never_invent_elapsed_time(tmp_path, kind):
    async def run():
        registry = load_agent_catalog()
        compiled = await context(registry)
        moment = [NOW + timedelta(seconds=1)]

        async def handler(invocation):
            moment[0] += timedelta(milliseconds=-250 if kind == "regression" else 250)
            return RegimeDistribution(label="TRENDING")

        path = tmp_path / "graphs.sqlite3"
        async with await open_runtime(path, registry, handler, clock=lambda: moment[0]) as runtime:
            await runtime.run("timing", compiled)
        if kind != "regression":

            def mutate(event):
                if kind == "legacy" or event["status"] == "CREATED":
                    event.pop("observed_at")

            rewrite_events(path, mutate)
        async with await open_runtime(path, registry, handler, clock=lambda: moment[0]) as runtime:
            trace = runtime.trace("timing")
            assert (
                trace.timing_state
                == {
                    "legacy": "UNAVAILABLE",
                    "partial": "PARTIAL",
                    "regression": "CLOCK_REGRESSION",
                }[kind]
            )
            assert trace.elapsed_seconds is None
            assert runtime.receipt("timing").status == "COMPLETED"
            assert (
                runtime._ledger.records_for_agent("A02", "0.1.0")[0].receipt.status == "COMPLETED"
            )

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["failed", "timeout", "unavailable", "uncertain"])
def test_failed_and_interrupted_runs_retain_original_transitions(tmp_path, kind):
    async def run():
        registry = load_agent_catalog()
        compiled = await context(registry)
        entered = asyncio.Event()

        async def handler(invocation):
            if kind == "timeout":
                await asyncio.sleep(5)
            if kind == "uncertain":
                entered.set()
                await asyncio.sleep(10)
            raise RuntimeError("private error")

        path = tmp_path / "graphs.sqlite3"
        async with await open_runtime(
            path, registry, None if kind == "unavailable" else handler
        ) as runtime:
            if kind == "uncertain":
                pending = asyncio.create_task(runtime.run("failure", compiled))
                await asyncio.wait_for(entered.wait(), timeout=5)
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
                interrupted = runtime.trace("failure")
                assert interrupted.status == "RUNNING"
                assert interrupted.elapsed_seconds is None
                await runtime.resume("failure")
            else:
                await runtime.run("failure", compiled)
            trace = runtime.trace("failure")
            assert (
                trace.status
                == {
                    "failed": "FAILED",
                    "timeout": "TIMEOUT",
                    "unavailable": "UNAVAILABLE",
                    "uncertain": "UNCERTAIN",
                }[kind]
            )
            assert trace.output_sha256 is None and trace.output_trace_id is None
            assert "private error" not in trace.model_dump_json()
            assert len(trace.events) == (2 if kind == "unavailable" else 3)
            assert (trace.elapsed_seconds is None) == (kind == "unavailable")

    asyncio.run(run())


@pytest.mark.parametrize("mutation", ["hash", "context", "time", "extra"])
def test_rehashed_historical_event_corruption_is_withheld(tmp_path, mutation):
    async def run():
        registry = load_agent_catalog()
        compiled = await context(registry)

        async def handler(invocation):
            return RegimeDistribution(label="TRENDING")

        path = tmp_path / "graphs.sqlite3"
        async with await open_runtime(path, registry, handler) as runtime:
            await runtime.run("corrupt", compiled)

        def mutate(event):
            if event["status"] != "CREATED":
                return
            if mutation == "hash":
                event["record_hash"] = "0" * 64
            elif mutation == "context":
                event["context_ref"] = "agent-context:" + "0" * 64
            elif mutation == "time":
                event["observed_at"] = "2026-01-01T12:00:00+05:30"
            else:
                event["private"] = "no"

        rewrite_events(path, mutate)
        async with await open_runtime(path, registry, handler) as runtime:
            with pytest.raises(ValueError):
                runtime.trace("corrupt")

    asyncio.run(run())


def test_trace_clock_failure_rolls_back_graph_and_audit_together(tmp_path):
    from ai_quant_os.graph_ledger import GraphLedger
    from ai_quant_os.context_checkpoints import ContextCheckpoint

    registry = load_agent_catalog()
    compiled = asyncio.run(context(registry))
    checkpoint = ContextCheckpoint(
        schema_version="1.0", manifest=registry.get("A02", "0.1.0"), context=compiled
    )
    path = tmp_path / "graphs.sqlite3"
    ledger = GraphLedger(path, clock=lambda: NOW.replace(tzinfo=None))
    try:
        with pytest.raises(ValueError, match="clock must be aware"):
            ledger.create("clock-error", checkpoint)
        with pytest.raises(KeyError):
            ledger.get("clock-error")
        assert ledger._db.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 0
    finally:
        ledger.close()


def test_corrupt_trace_returns_unavailable_and_does_not_disrupt_order_reads(tmp_path):
    args = dict(
        operator_token="fixture-supervisor",
        clock=lambda: API_NOW,
        paper_db_path=tmp_path / "paper.sqlite3",
    )
    with TestClient(create_app(**args)) as client:
        task_run(client, "DATA_REVIEW")
        with sqlite3.connect(tmp_path / "paper.agents.sqlite3") as db:
            db.execute(
                "UPDATE audit_events SET event_json='{}' WHERE event_key='graph:task-review:CREATED'"
            )
        response = client.get("/api/v1/agents/graph-runs/task-review/trace", headers=HEADERS)
        assert response.status_code == 503
        assert client.get("/api/v1/orders").status_code == 200


def test_trace_schema_and_claim_validation(tmp_path):
    from pathlib import Path
    from ai_quant_os.graph_trace import GraphTrace

    schema = Path(__file__).resolve().parents[1] / "docs/schemas/graph-trace.schema.json"
    assert json.loads(schema.read_text(encoding="utf-8")) == GraphTrace.model_json_schema()
    with TestClient(
        create_app(
            operator_token="fixture-supervisor",
            clock=lambda: API_NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        task_run(client, "DATA_REVIEW")
        raw = client.get("/api/v1/agents/graph-runs/task-review/trace", headers=HEADERS).json()
        for field, value in [
            ("elapsed_seconds", "100"),
            ("trace_id", "graph-trace:" + "0" * 64),
            ("status", "FAILED"),
            ("timing_state", "UNAVAILABLE"),
        ]:
            with pytest.raises(ValueError):
                GraphTrace.model_validate({**raw, field: value})


def test_failed_audit_commit_retains_original_timeline(tmp_path):
    from ai_quant_os.graph_ledger import GraphLedger
    from ai_quant_os.context_checkpoints import ContextCheckpoint

    registry = load_agent_catalog()
    compiled = asyncio.run(context(registry))
    checkpoint = ContextCheckpoint(
        schema_version="1.0", manifest=registry.get("A02", "0.1.0"), context=compiled
    )
    ledger = GraphLedger(tmp_path / "graphs.sqlite3", clock=lambda: NOW)
    try:
        ledger.create("rollback", checkpoint)
        original = ledger.trace("rollback")
        ledger._db.execute(
            "CREATE TRIGGER deny_transition BEFORE INSERT ON audit_events WHEN NEW.event_key='graph:rollback:RUNNING' BEGIN SELECT RAISE(ABORT,'injected'); END"
        )
        ledger._db.commit()
        with pytest.raises(sqlite3.Error):
            ledger.transition("rollback", "CREATED", "RUNNING")
        assert ledger.trace("rollback") == original
        assert ledger.get("rollback").receipt.status == "CREATED"
    finally:
        ledger.close()
