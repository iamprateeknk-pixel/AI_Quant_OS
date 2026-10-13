"""Evidence-linked paper fill observations for archived advisory agent runs."""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Context, Decimal, Inexact, localcontext
from typing import Literal

from ai_quant_os.agent_runs import AgentRun
from ai_quant_os.market import MarketSnapshot
from ai_quant_os.paper_attribution import OrderAttribution
from ai_quant_os.paper_attribution import _account, _fingerprint, _mark
from ai_quant_os.portfolio import PaperFill
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class _AgentReportModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


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


class AgentMarkedContribution(_AgentReportModel):
    run_id: str
    agent_id: str
    agent_version: str
    instrument_id: str
    quote_currency: str
    classification: str
    fill_count: int = Field(ge=0, strict=True)
    order_count: int = Field(ge=0, strict=True)
    net_quantity: Decimal = Field(allow_inf_nan=False)
    cash_flow_before_fees_quote: Decimal = Field(allow_inf_nan=False)
    fee_cash_flow_quote: Decimal = Field(allow_inf_nan=False)
    cash_flow_quote: Decimal = Field(allow_inf_nan=False)
    marked_inventory_quote: Decimal | None = Field(default=None, allow_inf_nan=False)
    marked_pnl_quote: Decimal | None = Field(default=None, allow_inf_nan=False)
    mark_price: Decimal | None = Field(default=None, allow_inf_nan=False)
    mark_observed_at: AwareDatetime | None = None
    mark_world_state_version: str | None = Field(default=None, min_length=1)
    mark_state: Literal["AVAILABLE", "MISSING", "UNTRUSTED", "FUTURE", "STALE"]

    @model_validator(mode="after")
    def marked_fields_match_state(self):
        present = (self.marked_inventory_quote, self.marked_pnl_quote, self.mark_price)
        if (self.mark_state == "AVAILABLE") != all(value is not None for value in present):
            raise ValueError("Agent contribution mark fields disagree with mark state")
        return self


class AgentInstrumentMarkedContribution(_AgentReportModel):
    instrument_id: str
    quote_currency: str
    fill_count: int = Field(ge=0, strict=True)
    order_count: int = Field(ge=0, strict=True)
    net_quantity: Decimal = Field(allow_inf_nan=False)
    cash_flow_before_fees_quote: Decimal = Field(allow_inf_nan=False)
    fee_cash_flow_quote: Decimal = Field(allow_inf_nan=False)
    cash_flow_quote: Decimal = Field(allow_inf_nan=False)
    marked_inventory_quote: Decimal | None = Field(default=None, allow_inf_nan=False)
    marked_pnl_quote: Decimal | None = Field(default=None, allow_inf_nan=False)
    mark_price: Decimal | None = Field(default=None, allow_inf_nan=False)
    mark_observed_at: AwareDatetime | None = None
    mark_world_state_version: str | None = Field(default=None, min_length=1)
    mark_state: Literal["AVAILABLE", "MISSING", "UNTRUSTED", "FUTURE", "STALE"]

    @model_validator(mode="after")
    def marked_fields_match_state(self):
        present = (self.marked_inventory_quote, self.marked_pnl_quote, self.mark_price)
        if (self.mark_state == "AVAILABLE") != all(value is not None for value in present):
            raise ValueError("Agent instrument mark fields disagree with mark state")
        return self


class AgentMarkedContributionReport(_AgentReportModel):
    schema_version: Literal["2.0"]
    mode: Literal["PAPER"]
    as_of: AwareDatetime
    ledger_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    accounting_scope: Literal["LINKED_AGENT_RUN_FILLS_ONLY"]
    marking_method: Literal["COMMON_ATTRIBUTED_PORTFOLIO_LIQUIDATION_MARK_V1"]
    coverage: Literal["COMPLETE", "PARTIAL", "NONE", "EMPTY"]
    associations: tuple[AgentMarkedContribution, ...]
    instruments: tuple[AgentInstrumentMarkedContribution, ...]
    unlinked_order_ids: tuple[str, ...]
    missing_run_ids: tuple[str, ...]
    ambiguous_order_ids: tuple[str, ...]
    unattributed_fill_ids: tuple[str, ...]
    evidence_gaps: tuple[str, ...]
    reconciled: Literal[True]
    causal_attribution: Literal[False]
    promotion_allowed: Literal[False]

    @model_validator(mode="after")
    def contribution_sums_reconcile(self):
        for instrument in self.instruments:
            rows = tuple(
                row for row in self.associations if row.instrument_id == instrument.instrument_id
            )
            for name in (
                "fill_count",
                "order_count",
                "net_quantity",
                "cash_flow_before_fees_quote",
                "fee_cash_flow_quote",
                "cash_flow_quote",
            ):
                if sum(
                    (getattr(row, name) for row in rows),
                    0 if name.endswith("count") else Decimal(0),
                ) != getattr(instrument, name):
                    raise ValueError("Agent contribution totals do not reconcile")
            if instrument.mark_state == "AVAILABLE" and (
                sum((row.marked_inventory_quote or Decimal(0) for row in rows), Decimal(0))
                != instrument.marked_inventory_quote
                or sum((row.marked_pnl_quote or Decimal(0) for row in rows), Decimal(0))
                != instrument.marked_pnl_quote
            ):
                raise ValueError("Marked agent contributions do not reconcile")
        return self


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


def build_marked_agent_contributions(
    owners,
    fills,
    intent_run_refs,
    runs,
    catalog,
    quotes,
    *,
    now: datetime,
    max_age_seconds: int = 5,
):
    base = build_agent_contributions(owners, fills, intent_run_refs, runs, catalog)
    if (
        not isinstance(now, datetime)
        or now.tzinfo is None
        or now.utcoffset() is None
        or type(quotes) is not tuple
        or len(quotes) > 16
        or any(type(quote) is not MarketSnapshot for quote in quotes)
        or type(max_age_seconds) is not int
        or not 1 <= max_age_seconds <= 300
    ):
        raise ValueError("Bounded marked agent attribution inputs required")

    linked_run_ids = {row.run_id for row in base.associations}
    run_orders = {run_id: [] for run_id in linked_run_ids}
    order_run = {}
    for owner in owners:
        refs = intent_run_refs.get(owner.order_id, ())
        if len(refs) != 1 or refs[0] not in linked_run_ids:
            continue
        run_id = refs[0]
        run = runs.get(run_id)
        if (
            run is None
            or run.run_id != run_id
            or run.decision.output.instrument_id != owner.instrument_id
            or run.recorded_at > owner.created_at
            or owner.created_at >= run.deep_review.expires_at
        ):
            continue
        order_run[owner.order_id] = run_id
        run_orders[run_id].append(owner)

    attributed_fills = {fill.fill_id: fill for fill in fills if fill.order_id in order_run}
    instrument_ids = sorted(
        {fill.instrument_id for fill in attributed_fills.values()}
        | {owner.instrument_id for owner in owners if owner.order_id in order_run}
    )
    price_by_instrument = {quote.instrument_id: quote for quote in quotes}
    if len(price_by_instrument) != len(quotes):
        raise ValueError("Duplicate agent attribution quote identity")
    instrument_by_id = {row.instrument_id: row for row in catalog}
    rows_by_run = {}
    totals = []
    evidence_gaps = []

    with localcontext(Context(prec=80)) as context:
        context.traps[Inexact] = True
        for instrument_id in instrument_ids:
            instrument_fills = tuple(
                fill for fill in attributed_fills.values() if fill.instrument_id == instrument_id
            )
            run_fills = {
                run_id: tuple(
                    fill for fill in instrument_fills if order_run[fill.order_id] == run_id
                )
                for run_id in linked_run_ids
            }
            portfolio_net = sum(
                (
                    fill.quantity if fill.side == "BUY" else -fill.quantity
                    for fill in instrument_fills
                ),
                Decimal(0),
            )
            mark, mark_state = _mark(
                price_by_instrument.get(instrument_id),
                portfolio_net,
                now,
                max_age_seconds,
            )
            mark_source = price_by_instrument.get(instrument_id)
            instrument = instrument_by_id[instrument_id]
            total_values = _account(instrument_fills, mark)
            total_orders = sum(
                owner.instrument_id == instrument_id
                for owner in owners
                if owner.order_id in order_run
            )
            totals.append(
                AgentInstrumentMarkedContribution(
                    **total_values,
                    instrument_id=instrument_id,
                    quote_currency=instrument.quote_currency,
                    order_count=total_orders,
                    mark_price=mark,
                    mark_observed_at=(mark_source.observed_at if mark_source is not None else None),
                    mark_world_state_version=(
                        mark_source.world_state_version if mark_source is not None else None
                    ),
                    mark_state=mark_state,
                )
            )
            for run_id in linked_run_ids:
                run = runs[run_id]
                run_owner_rows = tuple(
                    owner for owner in run_orders[run_id] if owner.instrument_id == instrument_id
                )
                if not run_owner_rows:
                    continue
                values = _account(run_fills[run_id], mark)
                rows_by_run[run_id, instrument_id] = AgentMarkedContribution(
                    run_id=run_id,
                    agent_id=run.decision.agent_id,
                    agent_version=run.decision.agent_version,
                    instrument_id=instrument_id,
                    quote_currency=instrument.quote_currency,
                    classification=run.decision.output.classification,
                    **values,
                    order_count=len(run_owner_rows),
                    mark_price=mark,
                    mark_observed_at=(mark_source.observed_at if mark_source is not None else None),
                    mark_world_state_version=(
                        mark_source.world_state_version if mark_source is not None else None
                    ),
                    mark_state=mark_state,
                )

        associations = []
        for run_id, base_row in ((row.run_id, row) for row in base.associations):
            covered_instruments = sorted({owner.instrument_id for owner in run_orders[run_id]})
            for instrument_id in covered_instruments:
                associations.append(rows_by_run[run_id, instrument_id])

    if any(row.mark_state != "AVAILABLE" for row in totals):
        evidence_gaps.append("CURRENT_MARK_EVIDENCE_MISSING")
    if base.unattributed_fill_ids:
        evidence_gaps.append("UNATTRIBUTED_FILLS_PRESENT")
    return AgentMarkedContributionReport(
        schema_version="2.0",
        mode="PAPER",
        as_of=now.astimezone(timezone.utc),
        ledger_sha256=_fingerprint(owners, fills),
        accounting_scope="LINKED_AGENT_RUN_FILLS_ONLY",
        marking_method="COMMON_ATTRIBUTED_PORTFOLIO_LIQUIDATION_MARK_V1",
        coverage=base.coverage,
        associations=tuple(
            sorted(associations, key=lambda row: (row.agent_id, row.run_id, row.instrument_id))
        ),
        instruments=tuple(totals),
        unlinked_order_ids=base.unlinked_order_ids,
        missing_run_ids=base.missing_run_ids,
        ambiguous_order_ids=base.ambiguous_order_ids,
        unattributed_fill_ids=base.unattributed_fill_ids,
        evidence_gaps=tuple(evidence_gaps),
        reconciled=True,
        causal_attribution=False,
        promotion_allowed=False,
    )
