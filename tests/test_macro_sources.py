import asyncio
from datetime import timedelta
from dataclasses import replace
import json

import pytest

from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
from ai_quant_os.world_state import WorldStateEngine
from ai_quant_os.policy_rates import normalize_ecb_rates
from test_policy_rates import NOW, recipe


def test_sources_bind_four_inputs_and_shared_world_observation():
    from ai_quant_os.macro_sources import macro_sources

    world = WorldStateEngine(PAPER_INSTRUMENTS).snapshot((), now=NOW)
    sources = macro_sources(
        world, normalize_ecb_rates(recipe(), received_at=NOW, capture_mode="LOCAL_CSV_IMPORT"), None
    )
    assert set(sources) == {"macro_feed", "event_feed", "rates_yields", "world_state"}
    for binding in sources.values():
        record = asyncio.run(binding.fetch())
        assert record.as_of == NOW and record.valid_until <= world.valid_until
    assert (
        json.loads(asyncio.run(sources["macro_feed"].fetch()).payload_json)["status"]
        == "UNAVAILABLE"
    )


@pytest.mark.parametrize("fault", ["reference", "status"])
def test_rate_evidence_cannot_forge_identity_or_refresh_old_receipt(fault):
    from ai_quant_os.macro_sources import MacroRatesEvidence

    capture = normalize_ecb_rates(
        recipe(), received_at=NOW - timedelta(days=1), capture_mode="LOCAL_CSV_IMPORT"
    )
    with pytest.raises(ValueError):
        MacroRatesEvidence(
            as_of=NOW,
            world_state_version="world:" + "0" * 64,
            capture=capture,
            capture_ref="rate-capture:" + "0" * 64 if fault == "reference" else capture.capture_ref,
            receipt_status="RECENT_RECEIPT" if fault == "status" else "OLD_RECEIPT",
        )


def test_world_source_refuses_changed_hash_bound_facts():
    from ai_quant_os.macro_sources import MacroWorldEvidence

    world = WorldStateEngine(PAPER_INSTRUMENTS).snapshot((), now=NOW)
    changed = replace(
        world,
        instruments=(replace(world.instruments[0], quote_status="FRESH"), *world.instruments[1:]),
    )
    with pytest.raises(ValueError):
        MacroWorldEvidence(world=changed)


def test_world_status_cannot_change_independently_of_its_measurements():
    from ai_quant_os.macro_sources import MacroWorldEvidence

    world = WorldStateEngine(PAPER_INSTRUMENTS).snapshot((), now=NOW)
    with pytest.raises(ValueError):
        MacroWorldEvidence(world=replace(world, status="READY"))
