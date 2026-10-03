import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from ai_quant_os.liquidity_sources import (
    BarsEvidence,
    QuotesEvidence,
    SpreadsEvidence,
    liquidity_sources,
    verified_liquidity_inputs,
)
from ai_quant_os.market import MarketSnapshot
from ai_quant_os.market_data import Bar
from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
from ai_quant_os.world_state import WorldStateEngine

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def source_models():
    engine = WorldStateEngine(PAPER_INSTRUMENTS)
    bars = tuple(
        Bar(
            "EURUSD",
            NOW - timedelta(seconds=3 - index),
            Decimal(2),
            Decimal(2),
            Decimal(2),
            Decimal(2),
            Decimal(1),
        )
        for index in range(4)
    )
    engine.record_bars(bars, now=NOW)
    quote = MarketSnapshot("EURUSD", Decimal(2), Decimal("2.002"), NOW, "fixture", True)
    world = engine.snapshot((quote,), now=NOW)
    sources = liquidity_sources(world, engine.histories(), (quote,))

    async def read():
        return [await sources[name].fetch() for name in ("quotes", "bars", "spreads")]

    records = asyncio.run(read())
    return (
        QuotesEvidence.model_validate_json(records[0].payload_json),
        BarsEvidence.model_validate_json(records[1].payload_json),
        SpreadsEvidence.model_validate_json(records[2].payload_json),
    )


@pytest.mark.parametrize("change", ["quote", "bars", "spread", "duplicate", "world"])
def test_changed_or_duplicated_source_cannot_reproduce_world_lineage(change):
    quotes, bars, spreads = source_models()
    assert len(verified_liquidity_inputs(quotes, bars, spreads)["EURUSD"]) == 4
    if change == "quote":
        quotes = quotes.model_copy(
            update={"quotes": (replace(quotes.quotes[0], ask=Decimal("2.003")),)}
        )
    elif change == "bars":
        history = bars.histories[0]
        changed = replace(history.bars[0], volume=Decimal(2))
        bars = bars.model_copy(
            update={
                "histories": (history.model_copy(update={"bars": (changed,) + history.bars[1:]}),)
            }
        )
    elif change == "spread":
        spreads = spreads.model_copy(
            update={
                "observations": (
                    spreads.observations[0].model_copy(update={"absolute_spread": Decimal(0)}),
                )
            }
        )
    elif change == "duplicate":
        quotes = quotes.model_copy(update={"quotes": quotes.quotes * 2})
    else:
        bars = bars.model_copy(update={"world_state_version": "another-world"})
    with pytest.raises(ValueError):
        verified_liquidity_inputs(quotes, bars, spreads)


def test_forged_depth_binding_is_rejected_even_when_quote_is_valid():
    from ai_quant_os.liquidity_sources import DisplayedDepth

    quotes, bars, spreads = source_models()
    depth = DisplayedDepth(
        instrument_id="EURUSD",
        quote_ref="paper-quote:forged",
        observed_at=NOW,
        bid_size=Decimal(3),
        ask_size=Decimal(1),
    )
    quotes = quotes.model_copy(update={"displayed_depth": (depth,)})
    with pytest.raises(ValueError, match="provenance"):
        verified_liquidity_inputs(quotes, bars, spreads)


def test_full_four_instrument_64_bar_context_fits_declared_budget():
    from ai_quant_os.agent_context import ContextCompiler
    from ai_quant_os.agent_registry import load_agent_catalog
    from ai_quant_os.liquidity_sources import LIQUIDITY_CONTEXT_BYTES, LIQUIDITY_INPUT_BYTES

    engine = WorldStateEngine(PAPER_INSTRUMENTS)
    bars = tuple(
        Bar(
            item.instrument_id,
            NOW - timedelta(seconds=63 - index),
            Decimal(2),
            Decimal(2),
            Decimal(2),
            Decimal(2),
            Decimal(1),
        )
        for item in PAPER_INSTRUMENTS
        for index in range(64)
    )
    engine.record_bars(bars, now=NOW)
    quotes = tuple(
        MarketSnapshot(item.instrument_id, Decimal(2), Decimal("2.002"), NOW, "fixture", True)
        for item in PAPER_INSTRUMENTS
    )
    world = engine.snapshot(quotes, now=NOW)
    context = asyncio.run(
        ContextCompiler(
            load_agent_catalog(),
            liquidity_sources(world, engine.histories(), quotes),
            clock=lambda: NOW,
            max_input_bytes=LIQUIDITY_INPUT_BYTES,
            max_context_bytes=LIQUIDITY_CONTEXT_BYTES,
        ).compile("A05", "0.2.0", task_id="full-window", scope="PAPER")
    )
    assert context.status == "READY"
    payload = next(row.payload_json for row in context.inputs if row.input_name == "bars")
    assert all(len(row.bars) == 64 for row in BarsEvidence.model_validate_json(payload).histories)
