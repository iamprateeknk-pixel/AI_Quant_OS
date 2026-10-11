"""Selected saved SDK spans are local observations, never delivery authority."""

from datetime import datetime, timezone
import json

from fastapi.testclient import TestClient

from ai_quant_os.api import create_app
from ai_quant_os.paper_telemetry import PaperTelemetry

TRACE = "1" * 32
ROOT = "2" * 16
CHILD = "3" * 16
NOW = datetime(2026, 10, 11, tzinfo=timezone.utc)
HEADERS = {"X-Operator-Token": "private-token"}
URL = "/api/v1/observability/traces/" + TRACE


def document():
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "ai-quant-paper"}},
                        {"key": "service.version", "value": {"stringValue": "0.1.0"}},
                        {"key": "ai_quant.mode", "value": {"stringValue": "PAPER"}},
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "ai_quant_os.paper", "version": "1.0"},
                        "spans": [
                            {
                                "traceId": TRACE,
                                "spanId": ROOT,
                                "name": "paper.http",
                                "kind": 2,
                                "startTimeUnixNano": "1791676800000000000",
                                "endTimeUnixNano": "1791676800000000300",
                                "attributes": [
                                    {"key": "ai_quant.mode", "value": {"stringValue": "PAPER"}}
                                ],
                                "status": {},
                            },
                            {
                                "traceId": TRACE,
                                "spanId": CHILD,
                                "parentSpanId": ROOT,
                                "name": "paper.risk.evaluate",
                                "kind": 1,
                                "startTimeUnixNano": "1791676800000000100",
                                "endTimeUnixNano": "1791676800000000200",
                                "attributes": [
                                    {"key": "ai_quant.mode", "value": {"stringValue": "PAPER"}},
                                    {
                                        "key": "ai_quant.risk.decision",
                                        "value": {"stringValue": "APPROVE"},
                                    },
                                ],
                                "status": {"message": "private-error"},
                                "events": [{"name": "private-event"}],
                            },
                        ],
                    }
                ],
            }
        ]
    }


def write(path, value=None):
    path.write_text(json.dumps(document() if value is None else value) + "\n", encoding="utf-8")


def app(path):
    return create_app(
        operator_token="private-token",
        clock=lambda: NOW,
        telemetry=PaperTelemetry(),
        collector_trace_file=path,
    )


def test_protected_selected_trace_preserves_original_parentage_and_source(tmp_path):
    path = tmp_path / "traces.jsonl"
    write(path)
    with TestClient(app(path)) as client:
        assert client.get(URL).status_code == 401
        response = client.get(URL, headers=HEADERS)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        value = response.json()
        assert value["state"] == "RECORDED_SPANS"
        assert value["trace_id"] == TRACE and value["delivery_verified"] is False
        assert value["independently_attested"] is False and value["complete_trace"] is False
        assert {span["span_id"] for span in value["spans"]} == {ROOT, CHILD}
        child = next(span for span in value["spans"] if span["span_id"] == CHILD)
        assert child["parent_span_id"] == ROOT and child["end_ns"] == "1791676800000000200"
        assert child["risk_decision"] == "APPROVE"
        assert "private" not in response.text and str(path) not in response.text
        assert value["files"][0]["prefix_bytes"] == path.stat().st_size


def test_missing_trace_does_not_claim_missing_export_or_complete_history(tmp_path):
    path = tmp_path / "traces.jsonl"
    write(path)
    with TestClient(app(path)) as client:
        value = client.get(URL.replace(TRACE, "4" * 32), headers=HEADERS).json()
        assert value["state"] == "NOT_OBSERVED" and value["spans"] == []
        assert value["complete_trace"] is False
        assert client.get(URL.replace(TRACE, "../private"), headers=HEADERS).status_code == 404


def test_corrupt_duplicate_or_partial_prefix_withholds_all_results(tmp_path):
    path = tmp_path / "traces.jsonl"
    for content in [
        "private-corrupt\n",
        json.dumps(document()),
        json.dumps(document()) + "\n" + json.dumps(document()) + "\n",
    ]:
        path.write_text(content, encoding="utf-8")
        with TestClient(app(path)) as client:
            value = client.get(URL, headers=HEADERS).json()
            assert value["state"] == "UNAVAILABLE" and value["spans"] == [] and value["files"] == []


def test_rotated_original_prefixes_are_scanned_without_inferred_parent(tmp_path):
    path = tmp_path / "traces.jsonl"
    data = document()
    data["resourceSpans"][0]["scopeSpans"][0]["spans"] = data["resourceSpans"][0]["scopeSpans"][0][
        "spans"
    ][1:]
    write(path, data)
    older = tmp_path / "traces-2026-10-11T00-00-00.001.jsonl"
    data = document()
    data["resourceSpans"][0]["scopeSpans"][0]["spans"] = data["resourceSpans"][0]["scopeSpans"][0][
        "spans"
    ][:1]
    write(older, data)
    with TestClient(app(path)) as client:
        value = client.get(URL, headers=HEADERS).json()
        assert (
            value["state"] == "RECORDED_SPANS"
            and len(value["spans"]) == 2
            and len(value["files"]) == 2
        )


def test_disabled_and_missing_file_infer_no_spans_or_prefix(tmp_path):
    for path, state in [(None, "DISABLED"), (tmp_path / "missing.jsonl", "UNAVAILABLE")]:
        with TestClient(app(path)) as client:
            value = client.get(URL, headers=HEADERS).json()
            assert value["state"] == state and value["spans"] == [] and value["files"] == []


def test_auth_before_read_and_actual_revocation_during_read_withhold_metadata(
    tmp_path, monkeypatch
):
    from ai_quant_os.collector_trace_source import CollectorTraceSource

    path = tmp_path / "traces.jsonl"
    write(path)
    original = CollectorTraceSource.read
    reads = []

    def capture(self, trace_id):
        reads.append(trace_id)
        return original(self, trace_id)

    monkeypatch.setattr(CollectorTraceSource, "read", capture)
    with TestClient(app(path)) as client:
        assert client.get(URL).status_code == 401 and reads == []
        assert client.get(URL.replace(TRACE, "0" * 32), headers=HEADERS).status_code == 422
        assert reads == []
        assert client.get(URL, headers=HEADERS).status_code == 200 and reads == [TRACE]
        session = client.post("/api/v1/operator/sessions", headers=HEADERS).json()["session_token"]
        headers = {"X-Operator-Session": session}

        def revoke(self, trace_id):
            result = original(self, trace_id)
            assert client.delete("/api/v1/operator/session", headers=headers).status_code == 200
            return result

        monkeypatch.setattr(CollectorTraceSource, "read", revoke)
        response = client.get(URL, headers=headers)
        assert response.status_code == 401 and "prefix_sha256" not in response.text


def test_original_prefix_append_is_accepted_but_rewrite_is_withheld(tmp_path, monkeypatch):
    import ai_quant_os.collector_trace_source as module

    path = tmp_path / "traces.jsonl"
    original = module.select_spans
    for mode in ["append", "rewrite"]:
        write(path)
        size = path.stat().st_size
        called = []

        def mutate(value, trace_id):
            result = original(value, trace_id)
            if not called:
                called.append(True)
                if mode == "append":
                    with path.open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(document()) + "\n")
                else:
                    write(path, {"resourceSpans": []})
            return result

        monkeypatch.setattr(module, "select_spans", mutate)
        with TestClient(app(path)) as client:
            value = client.get(URL, headers=HEADERS).json()
            if mode == "append":
                assert (
                    value["state"] == "RECORDED_SPANS" and value["files"][0]["prefix_bytes"] == size
                )
                assert path.stat().st_size > size
            else:
                assert (
                    value["state"] == "UNAVAILABLE"
                    and value["spans"] == []
                    and value["files"] == []
                )


def test_invalid_nanoseconds_cycles_and_retention_inventory_are_withheld(tmp_path):
    path = tmp_path / "traces.jsonl"
    for modification in ["time", "zero", "cycle", "source"]:
        data = document()
        spans = data["resourceSpans"][0]["scopeSpans"][0]["spans"]
        if modification == "time":
            spans[1]["endTimeUnixNano"] = "1791676800000000000"
        elif modification == "zero":
            spans[1]["spanId"] = "0" * 16
        elif modification == "cycle":
            spans[0]["parentSpanId"] = CHILD
        else:
            data["resourceSpans"][0]["scopeSpans"][0]["scope"]["name"] = "foreign"
        write(path, data)
        with TestClient(app(path)) as client:
            assert client.get(URL, headers=HEADERS).json()["state"] == "UNAVAILABLE"
    write(path)
    for index in range(4):
        write(tmp_path / f"traces-2026-10-11T00-00-00.00{index}.jsonl", {"resourceSpans": []})
    with TestClient(app(path)) as client:
        assert client.get(URL, headers=HEADERS).json()["state"] == "UNAVAILABLE"


def test_native_schema_and_actual_selected_response_are_exact(tmp_path):
    from pathlib import Path
    from jsonschema import Draft202012Validator
    from ai_quant_os.collector_trace_api import CollectorTraceObservation

    schema = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "docs/schemas/collector-trace-observation.schema.json"
        ).read_text(encoding="utf-8")
    )
    assert schema == CollectorTraceObservation.model_json_schema()
    Draft202012Validator.check_schema(schema)
    path = tmp_path / "traces.jsonl"
    write(path)
    with TestClient(app(path)) as client:
        Draft202012Validator(schema).validate(client.get(URL, headers=HEADERS).json())


def test_bounds_ambiguous_json_and_remote_configuration_never_disable_halt(tmp_path, monkeypatch):
    import ai_quant_os.collector_trace_source as module
    from pathlib import Path

    path = tmp_path / "traces.jsonl"
    path.write_text('{"resourceSpans":[],"resourceSpans":[]}\n', encoding="utf-8")
    with TestClient(app(path)) as client:
        assert client.get(URL, headers=HEADERS).json()["state"] == "UNAVAILABLE"
    with path.open("wb") as stream:
        stream.truncate(module.MAX_FILE + 1)
    with TestClient(app(path)) as client:
        assert client.get(URL, headers=HEADERS).json()["state"] == "UNAVAILABLE"
    write(path)
    ticks = iter([0, 100])
    monkeypatch.setattr(module, "monotonic", lambda: next(ticks))
    with TestClient(app(path)) as client:
        assert client.get(URL, headers=HEADERS).json()["state"] == "UNAVAILABLE"
    with TestClient(app(Path(r"\\private-host\private-share\traces.jsonl"))) as client:
        value = client.get(URL, headers=HEADERS)
        assert value.json()["state"] == "UNAVAILABLE" and "private-host" not in value.text
        response = client.post(
            "/api/v1/control/halt",
            headers={**HEADERS, "Idempotency-Key": "trace-source-halt"},
            json={"reason": "fixture"},
        )
        assert response.status_code == 200 and response.json()["safety_state"] == "HALT"
