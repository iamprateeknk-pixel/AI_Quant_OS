"""Original advisory evidence and observed SDK context commit together."""

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from ai_quant_os.paper_state_store import PaperStateStore
from ai_quant_os.api import create_app
from ai_quant_os.paper_telemetry import PaperTelemetry
from test_agent_runs import run
from test_agent_telemetry import invoke, NOW, HEADERS, PATH


def binding(item):
    from ai_quant_os.agent_trace_binding import make_agent_trace_binding

    return make_agent_trace_binding(
        item,
        {
            "runtime_id": "1" * 32,
            "sdk_version": "1.45.0",
            "sdk_trace_id": "2" * 32,
            "sdk_span_id": "3" * 16,
        },
    )


def test_actual_sdk_archive_binding_matches_original_trace_span_and_survives_restart(tmp_path):
    exporter = InMemorySpanExporter()
    telemetry = PaperTelemetry(exporter=exporter)
    try:
        response, _, _ = invoke(tmp_path, telemetry)
        assert telemetry.flush()
        store = PaperStateStore(tmp_path / "paper.sqlite3")
        item = store.agent_run(response.json()["trace_id"])
        link = store.agent_trace_binding(item.run_id)
        assert link is not None
        native = next(
            span for span in exporter.get_finished_spans() if span.name == "paper.agent.archive"
        )
        assert (
            link.sdk_trace_id
            == response.headers["X-Paper-Trace-Id"]
            == format(native.context.trace_id, "032x")
        )
        assert link.sdk_span_id == format(native.context.span_id, "016x")
        assert link.runtime_id == telemetry.diagnostics()["runtime_id"]
        assert link.run_sha256 == hashlib.sha256(item.model_dump_json().encode()).hexdigest()
        assert link.delivery_verified is False
        assert "private" not in link.model_dump_json()
        reopened = PaperStateStore(tmp_path / "paper.sqlite3")
        assert reopened.agent_trace_binding(item.run_id) == link
        assert reopened.agent_run(item.run_id) == item
        # The legacy public audit can expose the commitment, not private SDK identifiers.
        with TestClient(
            create_app(
                operator_token="private-token",
                clock=lambda: NOW,
                paper_db_path=tmp_path / "paper.sqlite3",
                telemetry=PaperTelemetry(),
            )
        ) as client:
            audit = client.get("/api/v1/audit")
            assert audit.status_code == 200
            assert link.sdk_trace_id not in audit.text and link.sdk_span_id not in audit.text
    finally:
        telemetry.close()


def test_exact_retry_retains_binding_and_new_context_cannot_replace_or_backfill(tmp_path):
    store = PaperStateStore(tmp_path / "paper.sqlite3")
    item = run()
    link = binding(item)
    assert store.record_agent_run(item, trace_binding=link)
    head = store.audit_integrity()
    assert head.valid and head.event_count == 2
    assert store.record_agent_run(item, trace_binding=link) is False
    assert store.record_agent_run(item) is False
    assert store.agent_trace_binding(item.run_id) == link and store.audit_integrity() == head
    changed = link.model_copy(update={"sdk_trace_id": "4" * 32})
    with pytest.raises(ValueError):
        store.record_agent_run(item, trace_binding=changed)
    assert store.agent_trace_binding(item.run_id) == link and store.audit_integrity() == head
    legacy = run()
    assert store.record_agent_run(legacy)
    previous = store.audit_integrity()
    with pytest.raises(ValueError):
        store.record_agent_run(legacy, trace_binding=binding(legacy))
    assert store.agent_trace_binding(legacy.run_id) is None and store.audit_integrity() == previous


@pytest.mark.parametrize("target", ["table", "audit"])
def test_binding_storage_fault_rolls_back_original_run_binding_and_both_events(tmp_path, target):
    path = tmp_path / "paper.sqlite3"
    store = PaperStateStore(path)
    item = run()
    with sqlite3.connect(path) as connection:
        if target == "table":
            connection.execute(
                "CREATE TRIGGER fail_trace BEFORE INSERT ON agent_trace_bindings BEGIN SELECT RAISE(FAIL,'private-storage-fault'); END"
            )
        else:
            connection.execute(
                "CREATE TRIGGER fail_trace BEFORE INSERT ON audit_events WHEN NEW.event_key LIKE 'agent-trace:%' BEGIN SELECT RAISE(FAIL,'private-storage-fault'); END"
            )
    with pytest.raises(sqlite3.Error):
        store.record_agent_run(item, trace_binding=binding(item))
    assert store.agent_run(item.run_id) is None
    assert store.agent_trace_binding(item.run_id) is None
    assert store.audit_integrity().valid and store.audit_integrity().event_count == 0


@pytest.mark.parametrize("mutation", ["edit", "delete"])
def test_changed_or_removed_binding_is_withheld_even_when_row_digest_is_recomputed(
    tmp_path, mutation
):
    path = tmp_path / "paper.sqlite3"
    store = PaperStateStore(path)
    item = run()
    store.record_agent_run(item, trace_binding=binding(item))
    with sqlite3.connect(path) as connection:
        if mutation == "delete":
            connection.execute("DELETE FROM agent_trace_bindings WHERE run_id=?", (item.run_id,))
        else:
            raw = binding(item).model_copy(update={"sdk_trace_id": "4" * 32}).model_dump_json()
            connection.execute(
                "UPDATE agent_trace_bindings SET binding_json=?,content_hash=? WHERE run_id=?",
                (raw, hashlib.sha256(raw.encode()).hexdigest(), item.run_id),
            )
    with pytest.raises(ValueError):
        store.agent_trace_binding(item.run_id)
    with pytest.raises(ValueError):
        store.agent_run(item.run_id)


def test_binding_requires_original_run_hash_and_nonzero_owned_context(tmp_path):
    from ai_quant_os.agent_trace_binding import make_agent_trace_binding

    store = PaperStateStore(tmp_path / "paper.sqlite3")
    item = run()
    link = binding(item)
    with pytest.raises(ValueError):
        store.record_agent_run(item, trace_binding=link.model_copy(update={"run_sha256": "0" * 64}))
    assert store.agent_run(item.run_id) is None and store.audit_integrity().event_count == 0
    assert make_agent_trace_binding(item, None) is None
    assert (
        make_agent_trace_binding(
            item,
            {
                "runtime_id": "1" * 32,
                "sdk_version": "1.45.0",
                "sdk_trace_id": "0" * 32,
                "sdk_span_id": "3" * 16,
            },
        )
        is None
    )


def test_sdk_context_can_only_be_bound_by_its_original_runtime():
    first = PaperTelemetry(exporter=InMemorySpanExporter())
    second = PaperTelemetry(exporter=InMemorySpanExporter())
    try:
        with first.span("paper.agent.archive", root=True) as span:
            context = first.binding_context(span)
            assert context["runtime_id"] == first.diagnostics()["runtime_id"]
            assert second.binding_context(span) is None
            assert PaperTelemetry().binding_context(span) is None
        first.close()
        # Retain an originally observed span identity after worker shutdown; no delivery inferred.
        assert first.binding_context(span) == context
    finally:
        first.close()
        second.close()


def test_native_api_binding_fault_withholds_archive_and_preserves_halt(tmp_path):
    path = tmp_path / "paper.sqlite3"
    store = PaperStateStore(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TRIGGER fail_trace BEFORE INSERT ON agent_trace_bindings BEGIN SELECT RAISE(FAIL,'private-binding-fault'); END"
        )
    exporter = InMemorySpanExporter()
    telemetry = PaperTelemetry(exporter=exporter)
    try:
        with TestClient(
            create_app(
                operator_token="private-token",
                clock=lambda: NOW,
                paper_db_path=path,
                telemetry=telemetry,
            )
        ) as client:
            response = client.post(
                PATH, headers=HEADERS, json={"instrument_id": "EURUSD", "task_id": "private-task"}
            )
            assert response.status_code == 503 and "private-binding-fault" not in response.text
            with sqlite3.connect(path) as connection:
                assert connection.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0] == 0
                assert (
                    connection.execute("SELECT COUNT(*) FROM agent_trace_bindings").fetchone()[0]
                    == 0
                )
            assert store.audit_integrity().valid and store.audit_integrity().event_count == 0
            halt = client.post(
                "/api/v1/control/halt",
                headers={**HEADERS, "Idempotency-Key": "private-halt"},
                json={"reason": "private-reason"},
            )
            assert halt.status_code == 200 and halt.json()["safety_state"] == "HALT"
        assert telemetry.flush()
        archive = next(
            span for span in exporter.get_finished_spans() if span.name == "paper.agent.archive"
        )
        assert archive.status.status_code.name == "ERROR" and not archive.events
        assert "private-binding-fault" not in " ".join(
            span.to_json() for span in exporter.get_finished_spans()
        )
    finally:
        telemetry.close()


def test_published_binding_schema_matches_native_model():
    from ai_quant_os.agent_trace_binding import AgentTraceBinding
    from jsonschema import Draft202012Validator

    schema = json.loads(
        (
            Path(__file__).resolve().parents[1] / "docs/schemas/agent-trace-binding.schema.json"
        ).read_text(encoding="utf-8")
    )
    assert schema == AgentTraceBinding.model_json_schema()
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(binding(run()).model_dump(mode="json"))


def test_failed_native_export_retains_original_binding_without_delivery_claim(tmp_path):
    class Failed(SpanExporter):
        def export(self, spans):
            return SpanExportResult.FAILURE

    telemetry = PaperTelemetry(exporter=Failed())
    try:
        response, _, _ = invoke(tmp_path, telemetry)
        assert telemetry.flush()
        link = PaperStateStore(tmp_path / "paper.sqlite3").agent_trace_binding(
            response.json()["trace_id"]
        )
        assert link.sdk_trace_id == response.headers["X-Paper-Trace-Id"]
        assert link.delivery_verified is False
        assert telemetry.diagnostics()["unconfirmed_spans"] > 0
    finally:
        telemetry.close()
