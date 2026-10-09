"""Compare reported money under explicit opening and component-allocation declarations."""

from datetime import datetime
from decimal import Context, Decimal, localcontext
from hashlib import sha256
from typing import Literal

from pydantic import Field, model_validator

from ai_quant_os.mt5_account_sync import DEAL_KINDS
from ai_quant_os.mt5_demo_gateway import canonical, utc
from ai_quant_os.mt5_demo_observation import ObservationModel
from ai_quant_os.mt5_sync_projection import ProjectionRejected

COMPONENTS = ("profit", "commission", "swap", "fee")
SOURCE_LIMIT = Decimal("1000000000000")
ROLLUP_LIMIT = Decimal("100000000000000000")
SourceMoney = Field(ge=-SOURCE_LIMIT, le=SOURCE_LIMIT, decimal_places=8, allow_inf_nan=False)
RollupMoney = Field(ge=-ROLLUP_LIMIT, le=ROLLUP_LIMIT, decimal_places=8, allow_inf_nan=False)
NonnegativeMoney = Field(ge=0, le=SOURCE_LIMIT, decimal_places=8, allow_inf_nan=False)
DealKind = Literal[*DEAL_KINDS]
Bucket = Literal["BALANCE", "CREDIT"]


class ComponentAllocation(ObservationModel):
    kind: DealKind
    profit: Bucket
    commission: Bucket
    swap: Bucket
    fee: Bucket


class DeclaredOpeningMoney(ObservationModel):
    schema_version: Literal["1.0"] = "1.0"
    account_scope_ref: str = Field(pattern=r"^[a-f0-9]{64}$")
    history_start: datetime
    boundary: Literal["IMMEDIATELY_BEFORE_HISTORY_START"] = "IMMEDIATELY_BEFORE_HISTORY_START"
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    balance: Decimal = SourceMoney
    credit: Decimal = NonnegativeMoney
    allocations: tuple[ComponentAllocation, ...] = Field(max_length=len(DEAL_KINDS))
    attestation: Literal["DECLARED_UNVERIFIED"] = "DECLARED_UNVERIFIED"

    @model_validator(mode="after")
    def unique_rules(self):
        utc(self.history_start)
        kinds = [rule.kind for rule in self.allocations]
        if len(kinds) != len(set(kinds)):
            raise ValueError("Component allocation kinds must be unique")
        return self


class MoneyKindTotal(ObservationModel):
    kind: DealKind
    deals: int = Field(ge=1, le=4096, strict=True)
    profit: Decimal = RollupMoney
    commission: Decimal = RollupMoney
    swap: Decimal = RollupMoney
    fee: Decimal = RollupMoney
    balance_movement: Decimal = RollupMoney
    credit_movement: Decimal = RollupMoney

    @model_validator(mode="after")
    def conserved_components(self):
        with localcontext(Context(prec=64)):
            reported = sum((getattr(self, field) for field in COMPONENTS), Decimal(0))
            if reported != self.balance_movement + self.credit_movement:
                raise ValueError("Allocated money disagrees with reported components")
        return self


class MoneySummary(ObservationModel):
    opening_balance: Decimal = SourceMoney
    opening_credit: Decimal = NonnegativeMoney
    balance_movement: Decimal = RollupMoney
    credit_movement: Decimal = RollupMoney
    expected_balance: Decimal = RollupMoney
    expected_credit: Decimal = RollupMoney
    observed_balance: Decimal = SourceMoney
    observed_credit: Decimal = NonnegativeMoney
    balance_difference: Decimal = RollupMoney
    credit_difference: Decimal = RollupMoney

    @model_validator(mode="after")
    def exact_differences(self):
        with localcontext(Context(prec=64)):
            for bucket in ("balance", "credit"):
                expected = getattr(self, "opening_" + bucket) + getattr(self, bucket + "_movement")
                if expected != getattr(self, "expected_" + bucket) or getattr(
                    self, "observed_" + bucket
                ) - expected != getattr(self, bucket + "_difference"):
                    raise ValueError("Inconsistent money difference")
        return self


class MoneyComparison(ObservationModel):
    schema_version: Literal["1.0"] = "1.0"
    observation_kind: Literal["DECLARED_OPENING_MONEY_COMPARISON"] = (
        "DECLARED_OPENING_MONEY_COMPARISON"
    )
    state: Literal["CONSISTENT_WITH_DECLARATION", "DIVERGENT", "WITHHELD"]
    code: (
        Literal[
            "STORAGE_UNAVAILABLE",
            "SOURCE_INVALID",
            "NO_HISTORY",
            "OPENING_MISMATCH",
            "MISSING_PRIOR_DEALS",
            "HISTORY_ENDPOINT",
            "CURRENCY_MISMATCH",
            "ARITHMETIC_BOUND",
            "UNCLASSIFIED_COMPONENTS",
            "CANCELLATION_ACCOUNTING_UNVERIFIED",
        ]
        | None
    ) = None
    declaration_ref: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_receipt_ref: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    account_scope_ref: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    currency: str | None = None
    history_start: datetime | None = None
    history_through: datetime | None = None
    account_observed_at: datetime | None = None
    revised_deals: int = Field(default=0, ge=0, le=4096, strict=True)
    cancelled_deals: int = Field(default=0, ge=0, le=4096, strict=True)
    rows: tuple[MoneyKindTotal, ...] = Field(default=(), max_length=len(DEAL_KINDS))
    summary: MoneySummary | None = None
    opening_attestation: Literal["DECLARED_UNVERIFIED"] = "DECLARED_UNVERIFIED"
    allocation_attestation: Literal["DECLARED_UNVERIFIED"] = "DECLARED_UNVERIFIED"
    history_attestation: Literal["UNVERIFIED"] = "UNVERIFIED"
    account_freshness: Literal["UNVERIFIED"] = "UNVERIFIED"
    consistency: Literal["NON_ATOMIC_SEQUENTIAL_READ"] = "NON_ATOMIC_SEQUENTIAL_READ"
    may_affect_portfolio: Literal[False] = False
    may_submit_orders: Literal[False] = False
    native_attestation: Literal["UNVERIFIED"] = "UNVERIFIED"

    @model_validator(mode="after")
    def consistent_state(self):
        if self.cancelled_deals and self.state != "WITHHELD":
            raise ValueError("Cancelled deal accounting is unverified")
        if self.state == "WITHHELD":
            if self.code is None or self.rows or self.summary is not None:
                raise ValueError("Withheld comparison must contain no arithmetic")
            return self
        if (
            self.code is not None
            or self.summary is None
            or len({row.kind for row in self.rows}) != len(self.rows)
        ):
            raise ValueError("Incomplete money comparison")
        with localcontext(Context(prec=64)):
            for bucket in ("balance", "credit"):
                if sum(
                    (getattr(row, bucket + "_movement") for row in self.rows), Decimal(0)
                ) != getattr(self.summary, bucket + "_movement"):
                    raise ValueError("Money summary disagrees with kind totals")
        divergent = self.summary.balance_difference != 0 or self.summary.credit_difference != 0
        if divergent != (self.state == "DIVERGENT"):
            raise ValueError("Money state disagrees with differences")
        return self


def _bounded(value):
    if (
        not value.is_finite()
        or value.copy_abs() > SOURCE_LIMIT
        or value != value.quantize(Decimal("0.00000001"))
    ):
        raise ProjectionRejected("ARITHMETIC_BOUND")
    return value


def _totals(versions, declaration):
    rules = {rule.kind: rule for rule in declaration.allocations}
    totals = {}
    for version in versions:
        deal = version.payload
        kind = deal.side if version.kind == "FILL" else deal.kind
        if deal.currency != declaration.currency:
            raise ProjectionRejected("CURRENCY_MISMATCH")
        if kind not in rules:
            raise ProjectionRejected("UNCLASSIFIED_COMPONENTS")
        row = totals.setdefault(
            kind,
            {
                "kind": kind,
                "deals": 0,
                **{c: Decimal(0) for c in (*COMPONENTS, "balance_movement", "credit_movement")},
            },
        )
        row["deals"] += 1
        for component in COMPONENTS:
            amount = _bounded(getattr(deal, component))
            row[component] += amount
            target = getattr(rules[kind], component).lower() + "_movement"
            row[target] += amount
    return tuple(MoneyKindTotal(**totals[kind]) for kind in sorted(totals))


def compare_money(store, connection, declaration: DeclaredOpeningMoney) -> MoneyComparison:
    reference = sha256(canonical(declaration.model_dump(mode="json")).encode()).hexdigest()
    metadata = {"declaration_ref": reference}
    try:
        view, versions = store.position_history(connection)
        if view is None:
            raise ProjectionRejected("NO_HISTORY")
        cancellations = sum(
            v.kind == "CASH" and v.payload.kind in {"BUY_CANCELED", "SELL_CANCELED"}
            for v in versions
        )
        metadata.update(
            source_receipt_ref=view.latest_receipt_ref,
            account_scope_ref=view.batch.account_scope_ref,
            currency=view.batch.account.currency,
            history_start=view.history_start,
            history_through=view.history_through,
            account_observed_at=view.batch.observed_at,
            revised_deals=view.revision_count,
            cancelled_deals=cancellations,
        )
        if (
            declaration.account_scope_ref != view.batch.account_scope_ref
            or declaration.history_start != view.history_start
            or declaration.currency != view.batch.account.currency
        ):
            raise ProjectionRejected("OPENING_MISMATCH")
        if view.missing_prior_deals:
            raise ProjectionRejected("MISSING_PRIOR_DEALS")
        if view.history_through != view.batch.history_end:
            raise ProjectionRejected("HISTORY_ENDPOINT")
        if cancellations:
            # Native cancellations zero old profit and post a separate balance adjustment.
            # Retained latest values alone cannot establish their original posted cash.
            raise ProjectionRejected("CANCELLATION_ACCOUNTING_UNVERIFIED")
        with localcontext(Context(prec=64)):
            rows = _totals(versions, declaration)
            amounts = {}
            for bucket in ("balance", "credit"):
                opening = _bounded(getattr(declaration, bucket))
                movement = sum((getattr(row, bucket + "_movement") for row in rows), Decimal(0))
                observed = _bounded(getattr(view.batch.account, bucket))
                amounts.update(
                    {
                        "opening_" + bucket: opening,
                        bucket + "_movement": movement,
                        "expected_" + bucket: opening + movement,
                        "observed_" + bucket: observed,
                        bucket + "_difference": observed - opening - movement,
                    }
                )
            summary = MoneySummary(**amounts)
        return MoneyComparison(
            state="DIVERGENT"
            if summary.balance_difference != 0 or summary.credit_difference != 0
            else "CONSISTENT_WITH_DECLARATION",
            rows=rows,
            summary=summary,
            **metadata,
        )
    except ProjectionRejected as exc:
        code = (
            "SOURCE_INVALID"
            if exc.code in {"FOREIGN_SCOPE", "HISTORY_GAP", "STALE_OBSERVATION", "CAPACITY"}
            else exc.code
        )
        return MoneyComparison(state="WITHHELD", code=code, **metadata)
    except Exception:
        return MoneyComparison(state="WITHHELD", code="STORAGE_UNAVAILABLE", **metadata)
