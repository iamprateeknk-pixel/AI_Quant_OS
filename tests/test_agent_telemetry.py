"""Actual advisory HTTP flow has native SDK parentage without provider payloads."""

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from ai_quant_os.agent_call_policy import AgentCallPolicy, AgentCallRule
from ai_quant_os.api import create_app
from ai_quant_os.paper_telemetry import PaperTelemetry
from ai_quant_os.provider_call_admission import ProviderOwner

NOW = datetime(2026, 10, 11, tzinfo=timezone.utc)
HEADERS = {"X-Operator-Token": "private-token"}
PATH = "/api/v1/paper/decisions/classify"


class Classifier:
    provider_id, provider_version = "fixture-classifier", "1"

    def __init__(self, failed=False, timeout=False):
        self.calls, self.failed = 0, failed
        self.timeout, self.cancelled = timeout, False

    async def classify(self, snapshot, instrument):
        self.calls += 1
        if self.timeout:
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled = True
        if self.failed:
            raise RuntimeError("private-provider-secret")
        item = next(row for row in snapshot.instruments if row.instrument_id == instrument)
        return {
            "schema_version": "1.0",
            "world_state_version": snapshot.world_state_version,
            "instrument_id": instrument,
            "classification": "TRENDING",
            "confidence": "0.9",
            "created_at": NOW,
            "expires_at": NOW + timedelta(seconds=4),
            "evidence_refs": item.evidence_refs,
        }


class Reviewer:
    provider_id, provider_version = "fixture-reviewer", "1"

    def __init__(self):
        self.calls = 0

    async def review(self, snapshot, decision):
        self.calls += 1
        return {
            "schema_version": "1.0",
            "world_state_version": snapshot.world_state_version,
            "instrument_id": decision.output.instrument_id,
            "parent_trace_id": decision.trace_id,
            "classification": "TRENDING",
            "confidence": "0.9",
            "evidence_refs": decision.evidence_refs,
            "contradictions": [],
            "rationale": "private-rationale",
            "created_at": NOW,
            "expires_at": NOW + timedelta(seconds=4),
        }


def invoke(tmp_path, telemetry, *, calls=10, failed=False, timeout=False):
    classifier, reviewer = Classifier(failed, timeout), Reviewer()
    app = create_app(
        operator_token="private-token",
        clock=lambda: NOW,
        paper_db_path=tmp_path / "paper.sqlite3",
        telemetry=telemetry,
        fast_classifier=classifier,
        deep_reviewer=reviewer,
        classification_owner=ProviderOwner("A02", "0.2.0"),
        review_owner=ProviderOwner("A03", "0.2.0"),
        agent_call_policy=AgentCallPolicy(
            tuple(AgentCallRule(f"A{i:02}", calls, 10, 0, 0) for i in range(1, 19))
        ),
    )
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/v1/paper/quotes",
                headers=HEADERS,
                json={
                    "instrument_id": "EURUSD",
                    "bid": "1.1000",
                    "ask": "1.1001",
                    "observed_at": NOW.isoformat(),
                    "world_state_version": "private-world",
                },
            ).status_code
            == 200
        )
        response = client.post(
            PATH, headers=HEADERS, json={"instrument_id": "EURUSD", "task_id": "private-task"}
        )
        assert response.status_code == 200
        archive = client.get("/api/v1/agents/runs/" + response.json()["trace_id"])
        assert archive.status_code == 200
        assert archive.json()["run"]["decision"]["trace_id"] == response.json()["trace_id"]
        assert client.get("/api/v1/orders").json()["orders"] == []
        assert (
            client.post(
                "/api/v1/control/halt",
                headers={**HEADERS, "Idempotency-Key": "private-halt"},
                json={"reason": "private-reason"},
            ).status_code
            == 200
        )
    return response, classifier, reviewer


def test_native_advisory_stages_and_provider_attempts_share_original_http_trace(tmp_path):
    exporter = InMemorySpanExporter()
    telemetry = PaperTelemetry(exporter=exporter)
    try:
        response, classifier, reviewer = invoke(tmp_path, telemetry)
        assert classifier.calls == reviewer.calls == 1
        assert response.json()["deep_review"]["output"]["status"] == "COMPLETED"
        assert telemetry.flush()
        spans = [
            span
            for span in exporter.get_finished_spans()
            if span.context.trace_id == int(response.headers["X-Paper-Trace-Id"], 16)
        ]
        indexed = {span.name: span for span in spans}
        assert set(indexed) == {
            "paper.http",
            "paper.market.snapshot",
            "paper.agent.classify",
            "paper.agent.review",
            "paper.agent.archive",
            "paper.provider.classify",
            "paper.provider.review",
        }
        root = indexed["paper.http"]
        assert root.parent is None
        for name in (
            "paper.market.snapshot",
            "paper.agent.classify",
            "paper.agent.review",
            "paper.agent.archive",
        ):
            assert indexed[name].parent.span_id == root.context.span_id
        assert (
            indexed["paper.provider.classify"].parent.span_id
            == indexed["paper.agent.classify"].context.span_id
        )
        assert (
            indexed["paper.provider.review"].parent.span_id
            == indexed["paper.agent.review"].context.span_id
        )
        assert (
            indexed["paper.agent.classify"].attributes["ai_quant.agent.envelope_id"]
            == response.json()["trace_id"]
        )
        assert (
            indexed["paper.agent.review"].attributes["ai_quant.agent.parent_envelope_id"]
            == response.json()["trace_id"]
        )
        assert (
            indexed["paper.agent.review"].attributes["ai_quant.agent.envelope_id"]
            == response.json()["deep_review"]["trace_id"]
        )
        assert indexed["paper.agent.review"].attributes["ai_quant.review.status"] == "COMPLETED"
        assert (
            indexed["paper.agent.archive"].attributes["ai_quant.agent.envelope_id"]
            == response.json()["trace_id"]
        )
        wire = " ".join(span.to_json() for span in exporter.get_finished_spans())
        for secret in (
            "private-token",
            "private-task",
            "private-world",
            "private-rationale",
            "private-reason",
            "private-halt",
            "fixture-classifier",
            "fixture-reviewer",
        ):
            assert secret not in wire
    finally:
        telemetry.close()


def test_native_timeout_cancels_original_provider_task_and_finishes_error_span(tmp_path):
    exporter = InMemorySpanExporter()
    telemetry = PaperTelemetry(exporter=exporter)
    try:
        response, classifier, reviewer = invoke(tmp_path, telemetry, timeout=True)
        assert classifier.calls == 1 and classifier.cancelled and reviewer.calls == 0
        assert "PRIMARY_TIMEOUT" in response.json()["output"]["fallback_reasons"]
        assert telemetry.flush()
        provider = next(
            span for span in exporter.get_finished_spans() if span.name == "paper.provider.classify"
        )
        assert provider.status.status_code.name == "ERROR"
        assert provider.status.description == "Paper operation failed" and not provider.events
    finally:
        telemetry.close()


def test_disabled_advisory_trace_preserves_completed_provider_and_archive_flow(tmp_path):
    response, classifier, reviewer = invoke(tmp_path, PaperTelemetry())
    assert classifier.calls == reviewer.calls == 1
    assert response.json()["deep_review"]["output"]["status"] == "COMPLETED"
    assert "X-Paper-Trace-Id" not in response.headers


def test_broken_sdk_span_creation_does_not_disable_advisory_or_safety_flow(tmp_path):
    exporter = InMemorySpanExporter()
    telemetry = PaperTelemetry(exporter=exporter)

    class Broken:
        def start_as_current_span(self, *args, **kwargs):
            raise RuntimeError("private-sdk-start")

    telemetry._tracer = Broken()
    try:
        response, classifier, reviewer = invoke(tmp_path, telemetry)
        assert classifier.calls == reviewer.calls == 1
        assert response.json()["deep_review"]["output"]["status"] == "COMPLETED"
        assert telemetry.diagnostics()["instrumentation_errors"] > 0
        assert telemetry.flush() and exporter.get_finished_spans() == ()
    finally:
        telemetry.close()


def test_denied_provider_admission_has_no_provider_invocation_span_or_calls(tmp_path):
    exporter = InMemorySpanExporter()
    telemetry = PaperTelemetry(exporter=exporter)
    try:
        response, classifier, reviewer = invoke(tmp_path, telemetry, calls=0)
        assert classifier.calls == reviewer.calls == 0
        assert "PRIMARY_FAILED" in response.json()["output"]["fallback_reasons"]
        assert telemetry.flush()
        names = [span.name for span in exporter.get_finished_spans()]
        assert (
            "paper.agent.classify" in names
            and "paper.provider.classify" not in names
            and "paper.provider.review" not in names
        )
    finally:
        telemetry.close()


def test_provider_exception_records_fixed_sdk_error_and_preserves_fallback_and_halt(tmp_path):
    exporter = InMemorySpanExporter()
    telemetry = PaperTelemetry(exporter=exporter)
    try:
        response, classifier, reviewer = invoke(tmp_path, telemetry, failed=True)
        assert classifier.calls == 1 and reviewer.calls == 0
        assert "PRIMARY_FAILED" in response.json()["output"]["fallback_reasons"]
        assert telemetry.flush()
        provider = next(
            span for span in exporter.get_finished_spans() if span.name == "paper.provider.classify"
        )
        assert (
            provider.status.status_code.name == "ERROR"
            and provider.status.description == "Paper operation failed"
        )
        assert not provider.events
        assert "private-provider-secret" not in " ".join(
            span.to_json() for span in exporter.get_finished_spans()
        )
    finally:
        telemetry.close()
