import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

from ai_quant_os.agent_context import ContextCompiler, EvidenceRecord, SourceBinding
from ai_quant_os.agent_registry import AgentToolBoundary, load_agent_catalog
from ai_quant_os.event_agent import event_implementation
from ai_quant_os.event_feed import EventFeed
from ai_quant_os.event_sources import (
    EventEvidence,
    PositionsEvidence,
    event_sources,
    EVENT_INPUT_BYTES,
    EVENT_CONTEXT_BYTES,
)
from ai_quant_os.graph_runtime import GraphRuntime
from ai_quant_os.portfolio import PaperFill

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def feed(*, severity="HIGH", kind="DETECTED", coverage_start=None):
    events = [
        {
            "event_id": "news-1",
            "kind": kind,
            "currencies": ["USD"],
            "severity": severity,
            "occurs_at": (NOW - timedelta(seconds=30)).isoformat(),
            "reported_at": NOW.isoformat(),
        }
    ]
    return EventFeed.model_validate(
        {
            "observed_at": NOW,
            "valid_until": NOW + timedelta(seconds=60),
            "coverage_start": coverage_start or NOW - timedelta(hours=1),
            "coverage_end": NOW + timedelta(days=1),
            "economic_calendar": events if kind == "SCHEDULED" else [],
            "event_classifications": events if kind == "DETECTED" else [],
        }
    )


async def run_graph(tmp_path, sources):
    registry = load_agent_catalog()
    context = await ContextCompiler(
        registry,
        sources,
        clock=lambda: NOW,
        max_input_bytes=EVENT_INPUT_BYTES,
        max_context_bytes=EVENT_CONTEXT_BYTES,
    ).compile("A06", "0.2.0", task_id="fixture", scope="PAPER")
    async with GraphRuntime.open(
        tmp_path / "graph.sqlite3",
        registry=registry,
        implementations={("A06", "0.2.0"): event_implementation(registry)},
        tools=AgentToolBoundary(registry, {}),
        clock=lambda: NOW,
    ) as runtime:
        return await runtime.run("fixture", context)


@pytest.mark.parametrize(
    "severity,coverage_start,eurusd,eurjpy",
    [
        ("HIGH", None, True, False),
        ("UNKNOWN", None, None, False),
        ("HIGH", NOW - timedelta(minutes=1), True, None),
    ],
)
def test_detected_severity_and_incomplete_coverage_are_disclosed(
    tmp_path, severity, coverage_start, eurusd, eurjpy
):
    receipt = asyncio.run(
        run_graph(
            tmp_path,
            event_sources(feed(severity=severity, coverage_start=coverage_start), (), (), now=NOW),
        )
    )
    assert receipt.status == "COMPLETED"
    output = json.loads(receipt.output_json)
    rows = {row["instrument_id"]: row for row in output["assessments"]}
    assert rows["EURUSD"]["blackout_recommended"] is eurusd
    assert rows["EURJPY"]["blackout_recommended"] is eurjpy
    assert output["measurements"][0]["kind"] == "DETECTED"


def test_changed_positions_cannot_reproduce_retained_ledger_fingerprint():
    fill = PaperFill("fill-1", "order-1", "EURUSD", "BUY", Decimal(10), Decimal(2), Decimal(0), NOW)
    sources = event_sources(None, (fill,), (), now=NOW)
    raw = asyncio.run(sources["positions"].fetch()).payload_json
    evidence = PositionsEvidence.model_validate_json(raw)
    changed = evidence.model_dump(mode="python")
    changed["fills"] = (replace(fill, quantity=Decimal(11)),)
    with pytest.raises(ValueError, match="evidence"):
        PositionsEvidence.model_validate(changed)


def test_payload_status_cannot_falsely_refresh_expired_feed():
    published = feed()
    with pytest.raises(ValueError):
        EventEvidence(
            as_of=NOW + timedelta(seconds=61),
            status="FRESH",
            feed=published,
            feed_ref=published.content_ref,
        )


def test_mismatched_calendar_and_news_fail_with_typed_graph_receipt(tmp_path):
    sources = event_sources(feed(), (), (), now=NOW)
    original = asyncio.run(sources["event_classifications"].fetch())
    raw = json.loads(original.payload_json)
    raw["feed_ref"] = "paper-events:forged"
    changed = EvidenceRecord(
        source_id="fault-fixture",
        as_of=NOW,
        valid_until=NOW + timedelta(seconds=5),
        trusted=True,
        payload_json=json.dumps(raw),
    )

    async def fetch():
        return changed

    sources["event_classifications"] = SourceBinding("WORLD_STATE_READ", "GENERAL", fetch)
    receipt = asyncio.run(run_graph(tmp_path, sources))
    assert receipt.status == "INVALID" and receipt.output_json is None


def test_full_128_event_profile_retains_all_measurements_and_pair_associations(tmp_path):
    events = [
        {
            "event_id": "E" + str(index).zfill(3) + "_" + "x" * 59,
            "kind": "SCHEDULED",
            "currencies": ["EUR", "GBP", "JPY", "USD"],
            "severity": "HIGH",
            "occurs_at": NOW + timedelta(seconds=300),
            "reported_at": NOW,
        }
        for index in range(128)
    ]
    snapshot = EventFeed(
        observed_at=NOW,
        valid_until=NOW + timedelta(seconds=60),
        coverage_start=NOW - timedelta(hours=1),
        coverage_end=NOW + timedelta(days=1),
        economic_calendar=events,
    )
    receipt = asyncio.run(run_graph(tmp_path, event_sources(snapshot, (), (), now=NOW)))
    assert receipt.status == "COMPLETED"
    output = json.loads(receipt.output_json)
    assert len(output["measurements"]) == 128
    assert all(row["event_indices"] == list(range(128)) for row in output["assessments"])
    assert {row["event_id"] for row in output["measurements"]} == {
        row["event_id"] for row in events
    }


@pytest.mark.parametrize("fault", ["association", "mode", "missing_clear", "metric"])
def test_output_contract_rejects_incoherent_associations_modes_or_measurements(tmp_path, fault):
    from ai_quant_os.event_agent import EventRiskState

    source_feed = None if fault == "missing_clear" else feed()
    receipt = asyncio.run(run_graph(tmp_path, event_sources(source_feed, (), (), now=NOW)))
    raw = json.loads(receipt.output_json)
    if fault == "association":
        raw["assessments"][0]["event_indices"] = []
    elif fault == "mode":
        raw["assessments"][0]["recommended_mode"] = "OBSERVE_ONLY"
    elif fault == "missing_clear":
        raw["assessments"][0]["blackout_recommended"] = False
        raw["assessments"][0]["recommended_mode"] = "OBSERVE_ONLY"
    else:
        raw["measurements"][0]["uncalibrated_score"] = "0"
    with pytest.raises(ValueError):
        EventRiskState.model_validate(raw)
