"""Read adapters for context evidence available from the current paper world state."""

import json
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal

from ai_quant_os.agent_context import EvidenceRecord, SourceBinding
from ai_quant_os.world_state import WorldState, fingerprint
from ai_quant_os.market_data import Bar
from ai_quant_os.regime_features import regime_features


def paper_context_sources(
    snapshot: WorldState, histories: dict[str, tuple[Bar, ...]] | None = None
) -> dict[str, SourceBinding]:
    def encode(value):
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, Decimal):
            return str(value)
        raise TypeError("Unsupported paper context value")

    def bind(name, payload, tool="WORLD_STATE_READ"):
        async def fetch():
            return EvidenceRecord(
                source_id=f"paper:{name}:{snapshot.world_state_version}",
                as_of=snapshot.as_of,
                valid_until=snapshot.valid_until,
                trusted=True,
                payload_json=json.dumps(payload, default=encode),
            )

        return SourceBinding(tool, "GENERAL", fetch)

    sources = {
        "world_state": bind("world_state", asdict(snapshot)),
        "currency_twins": bind(
            "currency_twins",
            {
                "source": snapshot.source,
                "currency_twins": [asdict(item) for item in snapshot.currency_twins],
            },
        ),
    }
    sources["session_state"] = bind(
        "session_state",
        {
            "world_state_version": snapshot.world_state_version,
            "as_of": snapshot.as_of,
            "clock_basis": "UTC",
            "calendar_status": "UNAVAILABLE",
            "event_status": "UNAVAILABLE",
            "crisis_status": "UNAVAILABLE",
        },
    )

    def windows(item):
        bars = (histories or {}).get(item.instrument_id, ())
        if not bars:
            return []
        reference = "paper-bars:" + fingerprint([asdict(bar) for bar in bars])
        if reference not in item.evidence_refs or any(
            bar.observed_at > snapshot.as_of for bar in bars
        ):
            raise ValueError("Paper history does not match world-state evidence")
        return [asdict(row) for row in regime_features(bars)]

    prices = [
        {
            "instrument_id": item.instrument_id,
            "mid_price": item.mid_price,
            "spread_bps": item.spread_bps,
            "quote_status": item.quote_status,
            "evidence_refs": item.evidence_refs,
            "horizons": windows(item),
        }
        for item in snapshot.instruments
        if item.quote_status == "FRESH"
    ]
    if prices:
        sources["price_features"] = bind(
            "price_features",
            {
                "source": snapshot.source,
                "world_state_version": snapshot.world_state_version,
                "features": prices,
            },
        )
    volatility = [
        {
            "instrument_id": item.instrument_id,
            "realized_volatility_bps": item.realized_volatility_bps,
            "bar_count": item.bar_count,
            "history_status": item.history_status,
            "evidence_refs": item.evidence_refs,
        }
        for item in snapshot.instruments
        if item.history_status == "FRESH"
    ]
    if volatility:
        sources["volatility_features"] = bind(
            "volatility_features",
            {
                "source": snapshot.source,
                "world_state_version": snapshot.world_state_version,
                "features": volatility,
            },
            "HISTORY_READ",
        )
    return sources
