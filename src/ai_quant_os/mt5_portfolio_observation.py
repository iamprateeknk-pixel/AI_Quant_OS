"""Private, unverified demo account portfolio observations; no risk/execution input."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from pydantic import UUID4, Field, model_validator

from ai_quant_os.mt5_account_sync import AccountView, PositionView, OrderView
from ai_quant_os.mt5_demo_gateway import utc
from ai_quant_os.mt5_demo_observation import ObservationModel
from ai_quant_os.mt5_publication import read_publication


class DemoPortfolioRecord(ObservationModel):
    broker_id: Literal["EXNESS_MT5_DEMO"] = "EXNESS_MT5_DEMO"
    mode: Literal["DEMO_OBSERVATION"] = "DEMO_OBSERVATION"
    source_attempt_id: UUID4
    source_receipt_ref: str = Field(pattern=r"^[a-f0-9]{64}$")
    account_scope_ref: str = Field(pattern=r"^[a-f0-9]{64}$")
    observed_at: datetime
    valid_until: datetime
    history_start: datetime
    history_through: datetime
    account: AccountView
    positions: tuple[PositionView, ...] = Field(max_length=8)
    orders: tuple[OrderView, ...] = Field(max_length=8)
    quantity_basis: Literal["CURRENT_SYMBOL_METADATA"] = "CURRENT_SYMBOL_METADATA"
    history_attestation: Literal["UNVERIFIED"] = "UNVERIFIED"
    account_freshness: Literal["UNVERIFIED"] = "UNVERIFIED"
    consistency: Literal["NON_ATOMIC_SEQUENTIAL_READ"] = "NON_ATOMIC_SEQUENTIAL_READ"
    may_affect_portfolio: Literal[False] = False
    may_submit_orders: Literal[False] = False
    native_attestation: Literal["UNVERIFIED"] = "UNVERIFIED"

    @model_validator(mode="after")
    def source_window(self):
        for value in (self.observed_at, self.valid_until, self.history_start, self.history_through):
            utc(value)
        if (
            not timedelta(0) < self.valid_until - self.observed_at <= timedelta(seconds=3)
            or not self.history_start < self.history_through <= self.observed_at
        ):
            raise ValueError("Invalid portfolio source interval")
        if (
            any(p.currency != self.account.currency for p in self.positions)
            or len({p.ticket for p in self.positions}) != len(self.positions)
            or len({p.identifier for p in self.positions}) != len(self.positions)
            or len({o.ticket for o in self.orders}) != len(self.orders)
        ):
            raise ValueError("Conflicting portfolio identities or currencies")
        return self


class PortfolioPublication(ObservationModel):
    schema_version: Literal["1.0"] = "1.0"
    observation_kind: Literal["LOCAL_UNVERIFIED_DEMO_PORTFOLIO"] = "LOCAL_UNVERIFIED_DEMO_PORTFOLIO"
    run_id: UUID4
    state: Literal["OBSERVED", "EMPTY", "WITHHELD"]
    code: Literal["SOURCE_UNAVAILABLE", "CLOCK"] | None = None
    published_at: datetime
    record: DemoPortfolioRecord | None = None
    may_affect_portfolio: Literal[False] = False
    may_submit_orders: Literal[False] = False
    native_attestation: Literal["UNVERIFIED"] = "UNVERIFIED"

    @model_validator(mode="after")
    def complete_frame(self):
        utc(self.published_at)
        if self.state == "OBSERVED":
            if (
                self.record is None
                or self.code is not None
                or self.published_at < self.record.observed_at
            ):
                raise ValueError("Invalid observed publication")
        elif self.record is not None or ((self.code is not None) != (self.state == "WITHHELD")):
            raise ValueError("Unavailable publication contains account data")
        return self


class DemoPortfolioView(ObservationModel):
    state: Literal["AVAILABLE", "STALE", "UNAVAILABLE"]
    publication: PortfolioPublication | None = None
    may_affect_portfolio: Literal[False] = False
    may_submit_orders: Literal[False] = False


def build_portfolio_publication(controller, run_id, *, clock=lambda: datetime.now(timezone.utc)):
    now = utc(clock())
    values = {"run_id": run_id, "published_at": now}
    try:
        view = controller.projection.view(controller.connection)
        if view is None:
            return PortfolioPublication(state="EMPTY", **values)
        batch = view.batch
        if now < batch.observed_at:
            return PortfolioPublication(state="WITHHELD", code="CLOCK", **values)
        record = DemoPortfolioRecord(
            source_attempt_id=view.latest_attempt_id,
            source_receipt_ref=view.latest_receipt_ref,
            account_scope_ref=batch.account_scope_ref,
            observed_at=batch.observed_at,
            valid_until=batch.valid_until,
            history_start=view.history_start,
            history_through=view.history_through,
            account=batch.account,
            positions=batch.positions,
            orders=batch.orders,
        )
        return PortfolioPublication(state="OBSERVED", record=record, **values)
    except Exception:
        return PortfolioPublication(state="WITHHELD", code="SOURCE_UNAVAILABLE", **values)


def read_portfolio(path: Path, *, clock=lambda: datetime.now(timezone.utc)) -> DemoPortfolioView:
    try:
        publication = read_publication(path, PortfolioPublication)
        now = utc(clock())
        if now < publication.published_at:
            raise ValueError("Future publication")
        if publication.state != "OBSERVED":
            return DemoPortfolioView(state="UNAVAILABLE", publication=publication)
        return DemoPortfolioView(
            state="AVAILABLE" if now < publication.record.valid_until else "STALE",
            publication=publication,
        )
    except Exception:
        return DemoPortfolioView(state="UNAVAILABLE")
