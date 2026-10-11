"""Console values originate from retained native SDK context and original journal."""

from pathlib import Path
import json

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from ai_quant_os.agent_trace_binding_api import observe_agent_trace, AgentTraceObservation
from ai_quant_os.agent_trace_binding import make_agent_trace_binding
from ai_quant_os.paper_state_store import PaperStateStore
from ai_quant_os.paper_telemetry import PaperTelemetry
from test_agent_telemetry import NOW
from test_agent_trace_binding import run

UNKNOWN = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def observations(tmp_path):
    telemetry = PaperTelemetry(exporter=InMemorySpanExporter())
    try:
        store = PaperStateStore(tmp_path / "paper.sqlite3")
        item = run()
        with telemetry.span("paper.agent.archive") as span:
            binding = make_agent_trace_binding(item, telemetry.binding_context(span))
            assert binding is not None
            store.record_agent_run(item, trace_binding=binding)
        run_id = item.run_id
        available = observe_agent_trace(store, run_id, lambda: NOW)
        assert available.binding.sdk_trace_id == span.trace_id
        legacy = run()
        store.record_agent_run(legacy)
        values = {
            "available": available,
            "not_recorded": observe_agent_trace(store, legacy.run_id, lambda: NOW),
            "not_found": observe_agent_trace(store, UNKNOWN, lambda: NOW),
            "unavailable": AgentTraceObservation(
                run_id=run_id, state="UNAVAILABLE", observed_at=NOW
            ),
        }
        return {key: value.model_dump(mode="json") for key, value in values.items()}
    finally:
        telemetry.close()


def test_console_fixture_matches_native_original_context_and_snapshot(tmp_path):
    fixture = json.loads(
        (
            Path(__file__).resolve().parents[1] / "apps/web/tests/fixtures/agent-trace-lookup.json"
        ).read_text(encoding="utf-8")
    )
    current = observations(tmp_path)
    for key, value in current.items():
        AgentTraceObservation.model_validate(value)
        AgentTraceObservation.model_validate(fixture[key])
        # Random native IDs and original content commitments vary across independent fixtures.
        for field in ["run_id", "source_head"]:
            if value[field] is not None:
                value[field] = fixture[key][field]
        if value["binding"] is not None:
            for field in ["run_id", "run_sha256", "runtime_id", "sdk_trace_id", "sdk_span_id"]:
                value["binding"][field] = fixture[key]["binding"][field]
        assert value == fixture[key]
