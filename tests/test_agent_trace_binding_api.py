"""Private exact-run lookup retains original prefix and withholds invalid sources."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from ai_quant_os.api import create_app
from ai_quant_os.paper_state_store import PaperStateStore
from ai_quant_os.paper_telemetry import PaperTelemetry
from test_agent_runs import run
from test_agent_telemetry import invoke, NOW, HEADERS

UNKNOWN = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def url(run_id):
    return "/api/v1/observability/agent-runs/" + run_id + "/trace-binding"


def test_native_binding_lookup_is_protected_uncached_and_bound_to_original_prefix(
    tmp_path, monkeypatch
):
    exporter = InMemorySpanExporter()
    telemetry = PaperTelemetry(exporter=exporter)
    try:
        original, _, _ = invoke(tmp_path, telemetry)
        run_id = original.json()["trace_id"]
        store = PaperStateStore(tmp_path / "paper.sqlite3")
        expected = store.agent_trace_binding(run_id)
        head = store.audit_integrity()
        capture = PaperStateStore.agent_trace_observation
        reads = []

        def spy(self, run_id):
            reads.append(run_id)
            return capture(self, run_id)

        monkeypatch.setattr(PaperStateStore, "agent_trace_observation", spy)
        with TestClient(
            create_app(
                operator_token="private-token",
                clock=lambda: NOW,
                paper_db_path=tmp_path / "paper.sqlite3",
                telemetry=PaperTelemetry(),
            )
        ) as client:
            assert client.get(url(run_id)).status_code == 401
            assert reads == []
            response = client.get(url(run_id), headers=HEADERS)
            assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
            value = response.json()
            assert value["state"] == "AVAILABLE"
            assert reads == [run_id]
            assert value["binding"] == expected.model_dump(mode="json")
            assert (
                value["source_head"] == head.head_hash
                and value["source_event_count"] == head.event_count
            )
            assert value["delivery_verified"] is False and value["independently_attested"] is False
            assert "private-token" not in response.text and "private-rationale" not in response.text
            assert store.audit_integrity() == head
    finally:
        telemetry.close()


def test_legacy_and_absent_run_states_keep_no_inferred_sdk_context(tmp_path):
    store = PaperStateStore(tmp_path / "paper.sqlite3")
    item = run()
    store.record_agent_run(item)
    with TestClient(
        create_app(
            operator_token="private-token",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
            telemetry=PaperTelemetry(),
        )
    ) as client:
        for run_id, state in [(item.run_id, "NOT_RECORDED"), (UNKNOWN, "NOT_FOUND")]:
            response = client.get(url(run_id), headers=HEADERS)
            assert response.status_code == 200 and response.json()["state"] == state
            assert (
                response.json()["binding"] is None
                and response.json()["source_head"] == store.audit_integrity().head_hash
            )
        assert client.get(url("private-invalid-id"), headers=HEADERS).status_code == 422


def test_revocation_during_original_read_withholds_entire_binding(tmp_path, monkeypatch):
    store = PaperStateStore(tmp_path / "paper.sqlite3")
    item = run()
    store.record_agent_run(item)
    capture = PaperStateStore.agent_trace_observation
    with TestClient(
        create_app(
            operator_token="private-token",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
            telemetry=PaperTelemetry(),
        )
    ) as client:
        session = client.post("/api/v1/operator/sessions", headers=HEADERS).json()["session_token"]
        headers = {"X-Operator-Session": session}

        def revoke(self, run_id):
            result = capture(self, run_id)
            assert client.delete("/api/v1/operator/session", headers=headers).status_code == 200
            return result

        monkeypatch.setattr(PaperStateStore, "agent_trace_observation", revoke)
        response = client.get(url(item.run_id), headers=headers)
        assert response.status_code == 401 and "source_head" not in response.text


def test_corrupt_binding_is_unavailable_without_reset_or_raw_error(tmp_path):
    exporter = InMemorySpanExporter()
    telemetry = PaperTelemetry(exporter=exporter)
    try:
        original, _, _ = invoke(tmp_path, telemetry)
        run_id = original.json()["trace_id"]
        with sqlite3.connect(tmp_path / "paper.sqlite3") as connection:
            connection.execute("DELETE FROM agent_trace_bindings WHERE run_id=?", (run_id,))
        with TestClient(
            create_app(
                operator_token="private-token",
                clock=lambda: NOW,
                paper_db_path=tmp_path / "paper.sqlite3",
                telemetry=PaperTelemetry(),
            )
        ) as client:
            response = client.get(url(run_id), headers=HEADERS)
            assert response.status_code == 200 and response.json()["state"] == "UNAVAILABLE"
            assert response.json()["binding"] is None and response.json()["source_head"] is None
    finally:
        telemetry.close()


def test_malformed_or_failed_source_is_withheld_and_does_not_disable_halt(tmp_path, monkeypatch):
    def failed(*args):
        raise ValueError("private-source-fault")

    with TestClient(
        create_app(
            operator_token="private-token",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
            telemetry=PaperTelemetry(),
        )
    ) as client:
        monkeypatch.setattr(PaperStateStore, "agent_trace_observation", failed)
        response = client.get(url(UNKNOWN), headers=HEADERS)
        assert response.status_code == 200 and response.json()["state"] == "UNAVAILABLE"
        assert "private-source-fault" not in response.text
        halt = client.post(
            "/api/v1/control/halt",
            headers={**HEADERS, "Idempotency-Key": "fixture-halt"},
            json={"reason": "fixture"},
        )
        assert halt.status_code == 200 and halt.json()["safety_state"] == "HALT"


def test_additive_lookup_schema_matches_native_model():
    from ai_quant_os.agent_trace_binding_api import AgentTraceObservation
    from jsonschema import Draft202012Validator

    schema = json.loads(
        (
            Path(__file__).resolve().parents[1] / "docs/schemas/agent-trace-observation.schema.json"
        ).read_text(encoding="utf-8")
    )
    assert schema == AgentTraceObservation.model_json_schema()
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(
        AgentTraceObservation(run_id=UNKNOWN, state="UNAVAILABLE", observed_at=NOW).model_dump(
            mode="json"
        )
    )


def test_future_or_mismatched_source_withholds_binding_and_prefix(tmp_path):
    from ai_quant_os.agent_trace_binding_api import observe_agent_trace
    from ai_quant_os.audit_chain import AuditIntegrity

    store = PaperStateStore(tmp_path / "paper.sqlite3")
    item = run()
    store.record_agent_run(item)
    head = store.audit_integrity()

    class Source:
        def agent_trace_observation(self, run_id):
            return item, None, head

    assert observe_agent_trace(Source(), item.run_id, lambda: NOW).state == "NOT_RECORDED"
    for run_id, now in [(UNKNOWN, NOW), (item.run_id, datetime(2025, 1, 1, tzinfo=timezone.utc))]:
        value = observe_agent_trace(Source(), run_id, lambda: now)
        assert value.state == "UNAVAILABLE" and value.source_head is None and value.binding is None
    for invalid in [
        AuditIntegrity(1, head.event_count, head.head_hash, 0),
        AuditIntegrity(True, True, head.head_hash, 0),
        AuditIntegrity(True, head.event_count, "private-source", 0),
    ]:
        head = invalid
        assert observe_agent_trace(Source(), item.run_id, lambda: NOW).state == "UNAVAILABLE"


def test_lookup_read_time_is_captured_after_original_snapshot(tmp_path, monkeypatch):
    from datetime import timedelta

    store = PaperStateStore(tmp_path / "paper.sqlite3")
    item = run()
    store.record_agent_run(item)
    time = {"at": item.recorded_at - timedelta(seconds=1)}
    capture = PaperStateStore.agent_trace_observation

    def advanced(self, run_id):
        result = capture(self, run_id)
        time["at"] = item.recorded_at
        return result

    monkeypatch.setattr(PaperStateStore, "agent_trace_observation", advanced)
    with TestClient(
        create_app(
            operator_token="private-token",
            clock=lambda: time["at"],
            paper_db_path=tmp_path / "paper.sqlite3",
            telemetry=PaperTelemetry(),
        )
    ) as client:
        response = client.get(url(item.run_id), headers=HEADERS)
        assert response.status_code == 200 and response.json()["state"] == "NOT_RECORDED"
        assert response.json()["observed_at"] == item.recorded_at.isoformat().replace("+00:00", "Z")
