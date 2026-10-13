"""Evidence-linked paper fill observations for archived advisory agent runs."""

from dataclasses import dataclass
from decimal import Context, Decimal, Inexact, localcontext
from typing import Literal

from ai_quant_os.agent_runs import AgentRun
from ai_quant_os.paper_attribution import OrderAttribution
from ai_quant_os.portfolio import PaperFill


@dataclass(frozen=True)
class AgentRunContribution:
    run_id: str
    agent_id: str
    agent_version: str
    instrument_id: str
    quote_currency: str
    classification: str
    fill_count: int
    order_count: int
    actual_cash_flow_quote: Decimal
    fee_cash_flow_quote: Decimal


@dataclass(frozen=True)
class AgentContributionReport:
    schema_version: Literal["1.0"]
    mode: Literal["PAPER"]
    coverage: Literal["COMPLETE", "PARTIAL", "NONE", "EMPTY"]
    associations: tuple[AgentRunContribution, ...]
    unlinked_order_ids: tuple[str, ...]
    missing_run_ids: tuple[str, ...]
    ambiguous_order_ids: tuple[str, ...]
    unattributed_fill_ids: tuple[str, ...]
    reconciled: Literal[True]
    causal_attribution: Literal[False]
    promotion_allowed: Literal[False]


def build_agent_contributions(owners, fills, intent_run_refs, runs, catalog):
    if (
        type(owners) is not tuple
        or type(fills) is not tuple
        or type(intent_run_refs) is not dict
        or type(runs) is not dict
        or type(catalog) is not tuple
        or len(owners) > 10000
        or len(fills) > 10000
        or len(runs) > 10000
        or any(type(row) is not OrderAttribution for row in owners)
        or any(type(row) is not PaperFill for row in fills)
        or any(type(row) is not AgentRun for row in runs.values())
        or len({row.order_id for row in owners}) != len(owners)
        or len({row.fill_id for row in fills}) != len(fills)
    ):
        raise ValueError("Bounded typed agent attribution evidence required")

    ownership = {row.order_id: row for row in owners}
    instruments = {row.instrument_id: row for row in catalog}
    if len(instruments) != len(catalog):
        raise ValueError("Duplicate agent attribution identity")
    quantities: dict[str, Decimal] = {}
    fills_by_order: dict[str, list[PaperFill]] = {}
    for fill in fills:
        owner = ownership.get(fill.order_id)
        if (
            owner is None
            or owner.status != "ACCEPTED"
            or owner.instrument_id != fill.instrument_id
            or owner.side != fill.side
            or fill.instrument_id not in instruments
            or not owner.created_at <= fill.observed_at <= owner.expires_at
        ):
            raise ValueError("Agent attribution fill lineage mismatch")
        quantities[owner.order_id] = quantities.get(owner.order_id, Decimal(0)) + fill.quantity
        if quantities[owner.order_id] > owner.quantity:
            raise ValueError("Agent attribution fills exceed owned quantity")
        fills_by_order.setdefault(owner.order_id, []).append(fill)

    if set(intent_run_refs) - ownership.keys() or any(
        type(refs) is not tuple
        or len(refs) > 32
        or any(not isinstance(ref, str) or not 1 <= len(ref) <= 128 for ref in refs)
        for refs in intent_run_refs.values()
    ):
        raise ValueError("Agent attribution references do not match owned intents")

    linked: dict[str, list[OrderAttribution]] = {}
    unlinked, missing, ambiguous, attributed_fills = [], set(), [], set()
    for owner in owners:
        refs = intent_run_refs.get(owner.order_id, ())
        valid = []
        for run_id in refs:
            run = runs.get(run_id)
            if run is None:
                missing.add(run_id)
                continue
            if (
                run.run_id != run_id
                or run.decision.output.instrument_id != owner.instrument_id
                or run.recorded_at > owner.created_at
                or owner.created_at >= run.deep_review.expires_at
            ):
                missing.add(run_id)
                continue
            valid.append(run_id)
        if len(refs) > 1:
            ambiguous.append(owner.order_id)
        elif len(valid) == 1:
            linked.setdefault(valid[0], []).append(owner)
            attributed_fills.update(fill.fill_id for fill in fills_by_order.get(owner.order_id, ()))
        elif not refs:
            unlinked.append(owner.order_id)

    totals = []
    with localcontext(Context(prec=80)) as context:
        context.traps[Inexact] = True
        for run_id, run_owners in sorted(linked.items()):
            run = runs[run_id]
            instrument_id = run.decision.output.instrument_id
            instrument = instruments[instrument_id]
            run_fills = tuple(
                fill for owner in run_owners for fill in fills_by_order.get(owner.order_id, ())
            )
            cash = sum(
                (
                    (fill.quantity if fill.side == "SELL" else -fill.quantity) * fill.price
                    - fill.fee
                    for fill in run_fills
                ),
                Decimal(0),
            )
            fees = -sum((fill.fee for fill in run_fills), Decimal(0))
            totals.append(
                AgentRunContribution(
                    run_id,
                    run.decision.agent_id,
                    run.decision.agent_version,
                    instrument_id,
                    instrument.quote_currency,
                    run.decision.output.classification,
                    len(run_fills),
                    len(run_owners),
                    cash,
                    fees,
                )
            )

    fill_ids = {fill.fill_id for fill in fills}
    unattributed = tuple(sorted(fill_ids - attributed_fills))
    attributed = tuple(fill for fill in fills if fill.fill_id in attributed_fills)
    with localcontext(Context(prec=80)) as context:
        context.traps[Inexact] = True
        expected_cash = sum(
            (
                (fill.quantity if fill.side == "SELL" else -fill.quantity) * fill.price - fill.fee
                for fill in attributed
            ),
            Decimal(0),
        )
        expected_fees = -sum((fill.fee for fill in attributed), Decimal(0))
    if (
        sum((row.actual_cash_flow_quote for row in totals), Decimal(0)) != expected_cash
        or sum((row.fee_cash_flow_quote for row in totals), Decimal(0)) != expected_fees
    ):
        raise ValueError("Agent-linked fill cash flows do not reconcile")
    coverage = (
        "EMPTY"
        if not fills
        else "NONE"
        if not attributed_fills
        else "COMPLETE"
        if attributed_fills == fill_ids
        else "PARTIAL"
    )
    return AgentContributionReport(
        "1.0",
        "PAPER",
        coverage,
        tuple(totals),
        tuple(sorted(unlinked)),
        tuple(sorted(missing)),
        tuple(sorted(ambiguous)),
        unattributed,
        True,
        False,
        False,
    )
