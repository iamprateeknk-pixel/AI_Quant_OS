import asyncio
from datetime import timedelta
from decimal import Decimal
import json

import pytest
from pydantic import ValidationError

from test_policy_rates import NOW, recipe


async def run(tmp_path, capture=None, feed=None, *, fault=False):
    from ai_quant_os.macro_sources import macro_sources, MACRO_INPUT_BYTES, MACRO_CONTEXT_BYTES
    from ai_quant_os.macro_agent import macro_implementation
    from ai_quant_os.world_state import WorldStateEngine
    from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
    from ai_quant_os.agent_context import ContextCompiler, EvidenceRecord, SourceBinding
    from ai_quant_os.agent_registry import load_agent_catalog, AgentToolBoundary
    from ai_quant_os.graph_runtime import GraphRuntime

    registry = load_agent_catalog()
    world = WorldStateEngine(PAPER_INSTRUMENTS).snapshot((), now=NOW)
    sources = macro_sources(world, capture, feed)
    if fault:
        original = await sources["rates_yields"].fetch()
        payload = json.loads(original.payload_json)
        payload["world_state_version"] = "world:" + "0" * 64
        record = EvidenceRecord(
            source_id="fault-fixture",
            as_of=NOW,
            valid_until=original.valid_until,
            trusted=True,
            payload_json=json.dumps(payload),
        )

        async def fetch():
            return record

        sources["rates_yields"] = SourceBinding("MACRO_READ", "GENERAL", fetch)
    context = await ContextCompiler(
        registry,
        sources,
        clock=lambda: NOW,
        max_input_bytes=MACRO_INPUT_BYTES,
        max_context_bytes=MACRO_CONTEXT_BYTES,
    ).compile("A03", "0.2.0", task_id="macro-fixture", scope="PAPER")
    async with GraphRuntime.open(
        tmp_path / "graph.sqlite3",
        registry=registry,
        implementations={("A03", "0.2.0"): macro_implementation(registry)},
        tools=AgentToolBoundary(registry, {}),
        clock=lambda: NOW,
    ) as graph:
        receipt = await graph.run("macro-1", context)
        assert receipt.status == ("INVALID" if fault else "COMPLETED")
    return context, receipt


def test_mismatched_source_world_produces_typed_invalid_receipt(tmp_path):
    _, receipt = asyncio.run(run(tmp_path, fault=True))
    assert receipt.status == "INVALID" and receipt.output_json is None


def test_actual_graph_returns_dated_eur_drivers_with_missing_bias_evidence(tmp_path):
    from ai_quant_os.policy_rates import normalize_ecb_rates
    from ai_quant_os.macro_agent import CurrencyMacroBias

    capture = normalize_ecb_rates(
        recipe(), received_at=NOW - timedelta(seconds=30), capture_mode="LOCAL_CSV_IMPORT"
    )
    context, receipt = asyncio.run(run(tmp_path, capture))
    output = CurrencyMacroBias.model_validate_json(receipt.output_json)
    assert len(output.drivers) == 3
    assert all(row.change_basis_points == Decimal(275) for row in output.drivers)
    assert output.rate_capture_ref == capture.capture_ref
    assert output.rate_receipt_status == "RECENT_RECEIPT"
    assert output.confidence == 0 and output.uncertainty == 1 and output.requires_escalation
    assert output.input_refs == output.evidence_refs == context.input_refs
    assert [row.currency for row in output.currencies] == ["EUR", "GBP", "JPY", "USD"]
    assert output.currencies[0].driver_indices == (0, 1, 2)
    assert all(row.bias == "UNASSESSED" and not row.may_add_risk for row in output.currencies)


@pytest.mark.parametrize("kind", ["missing", "old", "future"])
def test_missing_or_noncurrent_receipt_preserves_limits_without_refresh(tmp_path, kind):
    from ai_quant_os.policy_rates import normalize_ecb_rates

    capture = (
        None
        if kind == "missing"
        else normalize_ecb_rates(
            recipe(),
            received_at=NOW + timedelta(seconds=1) if kind == "future" else NOW - timedelta(days=1),
            capture_mode="LOCAL_CSV_IMPORT",
        )
    )
    _, receipt = asyncio.run(run(tmp_path, capture))
    output = json.loads(receipt.output_json)
    assert (
        output["rate_receipt_status"]
        == {"missing": "MISSING", "old": "OLD_RECEIPT", "future": "FUTURE_RECEIPT"}[kind]
    )
    assert len(output["drivers"]) == (3 if kind == "old" else 0)
    assert all(row["bias"] == "UNASSESSED" for row in output["currencies"])


def test_root_output_contract_rejects_forged_rate_metric(tmp_path):
    from ai_quant_os.policy_rates import normalize_ecb_rates
    from ai_quant_os.macro_agent import CurrencyMacroBias

    _, receipt = asyncio.run(
        run(
            tmp_path,
            normalize_ecb_rates(recipe(), received_at=NOW, capture_mode="LOCAL_CSV_IMPORT"),
        )
    )
    raw = json.loads(receipt.output_json)
    raw["drivers"][0]["change_basis_points"] = "999"
    with pytest.raises(ValidationError):
        CurrencyMacroBias.model_validate(raw)


@pytest.mark.parametrize("fault", ["missing_ref", "receipt_status", "empty_drivers"])
def test_root_output_contract_rejects_inconsistent_rate_receipt_claims(tmp_path, fault):
    from ai_quant_os.policy_rates import normalize_ecb_rates
    from ai_quant_os.macro_agent import CurrencyMacroBias

    capture = (
        None
        if fault == "missing_ref"
        else normalize_ecb_rates(recipe(), received_at=NOW, capture_mode="LOCAL_CSV_IMPORT")
    )
    _, receipt = asyncio.run(run(tmp_path, capture))
    raw = json.loads(receipt.output_json)
    if fault == "missing_ref":
        raw["rate_capture_ref"] = "rate-capture:" + "0" * 64
    elif fault == "receipt_status":
        raw["rate_receipt_status"] = "OLD_RECEIPT"
    else:
        raw["drivers"] = []
        raw["currencies"][0]["driver_indices"] = []
    with pytest.raises(ValidationError):
        CurrencyMacroBias.model_validate(raw)


def test_maximum_event_feed_preserves_context_and_counts_without_inferred_bias(tmp_path):
    from ai_quant_os.event_feed import EventFeed

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
    feed = EventFeed(
        observed_at=NOW,
        valid_until=NOW + timedelta(seconds=60),
        coverage_start=NOW - timedelta(hours=1),
        coverage_end=NOW + timedelta(days=1),
        economic_calendar=events,
    )
    context, receipt = asyncio.run(run(tmp_path, feed=feed))
    output = json.loads(receipt.output_json)
    assert all(
        row["scheduled_event_count"] == 128
        and row["detected_event_count"] == 0
        and row["bias"] == "UNASSESSED"
        for row in output["currencies"]
    )
    retained = json.loads(
        next(row.payload_json for row in context.inputs if row.input_name == "event_feed")
    )["evidence"]["feed"]["economic_calendar"]
    assert {row["event_id"] for row in retained} == {row["event_id"] for row in events}


@pytest.mark.parametrize("fault", ["missing_reference", "missing_counts", "excess_total"])
def test_root_contract_rejects_impossible_fresh_event_claims(tmp_path, fault):
    from ai_quant_os.macro_agent import CurrencyMacroBias

    _, receipt = asyncio.run(run(tmp_path))
    raw = json.loads(receipt.output_json)
    raw["event_feed_status"] = "FRESH"
    if fault != "missing_reference":
        raw["event_feed_ref"] = "paper-events:" + "0" * 64
    if fault == "excess_total":
        for row in raw["currencies"]:
            row["scheduled_event_count"] = 128
            row["detected_event_count"] = 128
    with pytest.raises(ValidationError):
        CurrencyMacroBias.model_validate(raw)
