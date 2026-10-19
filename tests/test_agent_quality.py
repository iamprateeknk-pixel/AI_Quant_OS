from datetime import datetime, timedelta, timezone

import pytest

from ai_quant_os.agent_quality import operational_quality_observation
from ai_quant_os.agent_registry import load_agent_catalog
from ai_quant_os.graph_ledger import GraphReceipt

NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)


def receipt(registry, run_id, status):
    manifest = registry.get("A02", "0.2.0")
    return GraphReceipt(
        run_id=run_id,
        agent_id=manifest.agent_id,
        agent_version=manifest.agent_version,
        task_id=f"task-{run_id}",
        context_ref=f"context-{run_id}",
        manifest_ref=manifest.manifest_ref,
        input_refs=(),
        created_at=NOW,
        valid_until=NOW + timedelta(seconds=5),
        status=status,
        output_type=manifest.output_type,
        output_json="{}" if status == "COMPLETED" else None,
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
