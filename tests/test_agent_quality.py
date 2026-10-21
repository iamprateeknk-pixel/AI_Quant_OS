from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json

import pytest

from ai_quant_os.agent_quality import operational_quality_observation
from ai_quant_os.agent_registry import load_agent_catalog
from ai_quant_os.graph_ledger import GraphReceipt
from ai_quant_os.graph_trace import GraphTrace, GraphTraceEvent, trace_identity

NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)


def receipt(registry, run_id, status):
    manifest = registry.get("A02", "0.2.0")
    return GraphReceipt(
        run_id=run_id,
        agent_id=manifest.agent_id,
        agent_version=manifest.agent_version,
        task_id=f"task-{run_id}",
        context_ref="agent-context:" + sha256(run_id.encode()).hexdigest(),
        manifest_ref=manifest.manifest_ref,
        input_refs=(),
        created_at=NOW,
        valid_until=NOW + timedelta(seconds=5),
        status=status,
        output_type=manifest.output_type,
        output_json="{}" if status == "COMPLETED" else None,
    )


def trace_for(row, duration_seconds, status=None):
    start = NOW + timedelta(seconds=1)
    finish = start + timedelta(seconds=duration_seconds)
    status = status or row.status
    events = tuple(
        GraphTraceEvent(
            sequence=sequence,
            status=state,
            record_hash=sha256(f"{row.run_id}:{sequence}:record".encode()).hexdigest(),
            event_hash=sha256(f"{row.run_id}:{sequence}:event".encode()).hexdigest(),
            observed_at=at,
        )
        for sequence, state, at in (
            (1, "CREATED", NOW),
            (2, "RUNNING", start),
            (3, status, finish),
        )
    )
    return GraphTrace(
        scope="PAPER",
        trace_id=trace_identity(row.run_id, row.context_ref, row.manifest_ref),
        run_id=row.run_id,
        task_id=row.task_id,
        agent_id=row.agent_id,
        agent_version=row.agent_version,
        context_ref=row.context_ref,
        manifest_ref=row.manifest_ref,
        input_refs=row.input_refs,
        context_observed_at=NOW,
        context_valid_until=row.valid_until,
        status=status,
        output_sha256=(
            sha256(row.output_json.encode()).hexdigest()
            if status == "COMPLETED" and row.output_json
            else None
        ),
        events=events,
        timing_state="AVAILABLE",
        elapsed_seconds=Decimal(duration_seconds),
    )


def test_operational_quality_counts_bound_graph_outcomes_without_promotion():
    registry = load_agent_catalog()
    snapshot = operational_quality_observation(
        registry,
        (receipt(registry, "run-a", "COMPLETED"), receipt(registry, "run-b", "FAILED")),
        now=NOW,
    )
    row = snapshot.rows[0]
    assert snapshot.availability == "AVAILABLE"
    assert row.agent_id == "A02" and row.agent_version == "0.2.0"
    assert row.run_count == 2
    assert row.status_counts["COMPLETED"] == 1
    assert row.status_counts["FAILED"] == 1
    assert row.completion_rate == 0.5
    assert snapshot.calibration_established is False
    assert snapshot.promotion_allowed is False


def test_operational_quality_reports_verified_trace_duration_without_calibration():
    registry = load_agent_catalog()
    completed = receipt(registry, "run-a", "COMPLETED")
    failed = receipt(registry, "run-b", "FAILED")
    snapshot = operational_quality_observation(
        registry,
        (completed, failed),
        traces=(trace_for(completed, 2), trace_for(failed, 4)),
        now=NOW + timedelta(seconds=10),
    )

    row = snapshot.rows[0]
    assert row.timing_sample_count == 2
    assert row.mean_duration_seconds == Decimal(3)
    assert row.p95_duration_seconds == Decimal(4)
    assert snapshot.source_trace_count == 2
    assert (
        snapshot.source_traces_sha256
        == sha256(
            json.dumps(
                [
                    {
                        "run_id": row.run_id,
                        "status": row.status,
                        "timing_state": row.timing_state,
                        "elapsed_seconds": str(row.elapsed_seconds),
                        "event_hashes": [event.event_hash for event in row.events],
                    }
                    for row in sorted(
                        (trace_for(completed, 2), trace_for(failed, 4)),
                        key=lambda item: item.run_id,
                    )
                ],
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
    )
    assert snapshot.calibration_established is False
    assert snapshot.promotion_allowed is False


def test_operational_quality_rejects_trace_that_does_not_match_receipt():
    registry = load_agent_catalog()
    row = receipt(registry, "run-a", "COMPLETED")
    wrong = trace_for(row, 2, status="FAILED")

    with pytest.raises(ValueError, match="trace.*receipt|receipt.*trace"):
        operational_quality_observation(registry, (row,), traces=(wrong,), now=NOW)


def test_quality_snapshot_is_explicit_when_history_or_graph_store_is_missing():
    registry = load_agent_catalog()
    empty = operational_quality_observation(registry, (), now=NOW)
    missing = operational_quality_observation(registry, None, now=NOW)
    assert empty.availability == "NO_OBSERVATIONS"
    assert empty.reason == "NO_GRAPH_RUNS"
    assert missing.availability == "UNAVAILABLE"
    assert missing.reason == "GRAPH_LEDGER_UNAVAILABLE"
    assert not empty.rows and not missing.rows


def test_quality_snapshot_refuses_duplicate_or_wrong_manifest_receipts():
    registry = load_agent_catalog()
    row = receipt(registry, "run-a", "COMPLETED")
    with pytest.raises(ValueError, match="duplicate"):
        operational_quality_observation(registry, (row, row), now=NOW)
    substituted = row.model_copy(update={"manifest_ref": "agent-manifest:" + "0" * 64})
    with pytest.raises(ValueError, match="manifest"):
        operational_quality_observation(registry, (substituted,), now=NOW)
