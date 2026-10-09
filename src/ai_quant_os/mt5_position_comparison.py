"""Descriptive lot comparison from retained history and declared opening positions."""

from datetime import datetime
from decimal import Context, Decimal, localcontext
from hashlib import sha256
from typing import Literal

from pydantic import Field, model_validator

from ai_quant_os.mt5_demo_gateway import canonical, utc
from ai_quant_os.mt5_demo_observation import ObservationModel
from ai_quant_os.mt5_sync_projection import ProjectionRejected

MAX_ROWS = 128
LOT_LIMIT = Decimal("1000000000")
Lot = Field(ge=-LOT_LIMIT, le=LOT_LIMIT, decimal_places=8, allow_inf_nan=False)


class OpeningPosition(ObservationModel):
    instrument_id: str = Field(min_length=1, max_length=64)
    position_identifier: int = Field(gt=0, strict=True)
    signed_lots: Decimal = Lot


class DeclaredOpeningPositions(ObservationModel):
    schema_version: Literal["1.0"] = "1.0"
    account_scope_ref: str = Field(pattern=r"^[a-f0-9]{64}$")
    history_start: datetime
    boundary: Literal["IMMEDIATELY_BEFORE_HISTORY_START"] = "IMMEDIATELY_BEFORE_HISTORY_START"
    positions: tuple[OpeningPosition, ...] = Field(max_length=MAX_ROWS)
    attestation: Literal["DECLARED_UNVERIFIED"] = "DECLARED_UNVERIFIED"

    @model_validator(mode="after")
    def unique_identities(self):
        utc(self.history_start)
        ids = [p.position_identifier for p in self.positions]
        if len(ids) != len(set(ids)):
            raise ValueError("Opening identifiers must be unique")
        return self


class PositionDifference(ObservationModel):
    instrument_id: str
    position_identifier: int = Field(gt=0, strict=True)
    opening_lots: Decimal
    recorded_movement_lots: Decimal
    expected_lots: Decimal
    observed_lots: Decimal
    difference_lots: Decimal
    fill_count: int = Field(ge=0, le=4096, strict=True)

    @model_validator(mode="after")
    def exact_difference(self):
        values = (
            self.opening_lots,
            self.recorded_movement_lots,
            self.expected_lots,
            self.observed_lots,
            self.difference_lots,
        )
        if any(not v.is_finite() or v.copy_abs() > Decimal("5000000000000") for v in values):
            raise ValueError("Comparison arithmetic bound")
        with localcontext(Context(prec=64)):
            if any(v != v.quantize(Decimal("0.00000001")) for v in values):
                raise ValueError("Comparison lot scale")
            if (
                self.opening_lots + self.recorded_movement_lots != self.expected_lots
                or self.observed_lots - self.expected_lots != self.difference_lots
            ):
                raise ValueError("Inconsistent lot difference")
        return self


class PositionComparison(ObservationModel):
    schema_version: Literal["1.0"] = "1.0"
    observation_kind: Literal["DECLARED_OPENING_LOT_COMPARISON"] = "DECLARED_OPENING_LOT_COMPARISON"
    state: Literal["CONSISTENT_WITH_DECLARATION", "DIVERGENT", "WITHHELD"]
    code: (
        Literal[
            "STORAGE_UNAVAILABLE",
            "SOURCE_INVALID",
            "NO_HISTORY",
            "OPENING_MISMATCH",
            "MISSING_PRIOR_DEALS",
            "POSITION_IDENTITY",
            "UNMAPPED_INSTRUMENT",
            "HISTORY_ENDPOINT",
            "ARITHMETIC_BOUND",
            "CAPACITY",
        ]
        | None
    ) = None
    opening_ref: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_receipt_ref: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    account_scope_ref: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    history_start: datetime | None = None
    history_through: datetime | None = None
    position_observed_at: datetime | None = None
    rows: tuple[PositionDifference, ...] = Field(default=(), max_length=MAX_ROWS)
    revised_deals: int = Field(default=0, ge=0, le=4096, strict=True)
    cancelled_deals: int = Field(default=0, ge=0, le=4096, strict=True)
    opening_attestation: Literal["DECLARED_UNVERIFIED"] = "DECLARED_UNVERIFIED"
    history_attestation: Literal["UNVERIFIED"] = "UNVERIFIED"
    consistency: Literal["NON_ATOMIC_SEQUENTIAL_READ"] = "NON_ATOMIC_SEQUENTIAL_READ"
    may_affect_portfolio: Literal[False] = False
    may_submit_orders: Literal[False] = False
    native_attestation: Literal["UNVERIFIED"] = "UNVERIFIED"

    @model_validator(mode="after")
    def consistent_state(self):
        if self.state == "WITHHELD":
            if self.code is None or self.rows:
                raise ValueError("Withheld comparisons require a code and no rows")
        elif self.code is not None or (
            any(r.difference_lots != 0 for r in self.rows) != (self.state == "DIVERGENT")
        ):
            raise ValueError("Comparison state disagrees with rows")
        return self


def _bounded_lots(value):
    if (
        not value.is_finite()
        or abs(value) > LOT_LIMIT
        or value != value.quantize(Decimal("0.00000001"))
    ):
        raise ProjectionRejected("ARITHMETIC_BOUND")
    return value


def _rows(view, versions, declaration, instruments):
    opening, movement, observed, counts, identity = {}, {}, {}, {}, {}

    def key(instrument, identifier):
        if identifier <= 0 or (identifier in identity and identity[identifier] != instrument):
            raise ProjectionRejected("POSITION_IDENTITY")
        if instrument not in instruments:
            raise ProjectionRejected("UNMAPPED_INSTRUMENT")
        identity[identifier] = instrument
        return instrument, identifier

    for position in declaration.positions:
        opening[key(position.instrument_id, position.position_identifier)] = _bounded_lots(
            position.signed_lots
        )
    for version in versions:
        if version.kind != "FILL":
            continue
        fill = version.payload
        k = key(fill.instrument_id, fill.position_identifier)
        lots = _bounded_lots(fill.volume_lots)
        movement[k] = movement.get(k, Decimal(0)) + (lots if fill.side == "BUY" else -lots)
        counts[k] = counts.get(k, 0) + 1
    for position in view.batch.positions:
        k = key(position.instrument_id, position.identifier)
        if k in observed:
            raise ProjectionRejected("POSITION_IDENTITY")
        lots = _bounded_lots(position.volume_lots)
        observed[k] = lots if position.side == "BUY" else -lots
    keys = set(opening) | set(movement) | set(observed)
    if len(keys) > MAX_ROWS:
        raise ProjectionRejected("CAPACITY")
    rows = []
    for k in sorted(keys):
        start, change, actual = (
            opening.get(k, Decimal(0)),
            movement.get(k, Decimal(0)),
            observed.get(k, Decimal(0)),
        )
        expected = start + change
        rows.append(
            PositionDifference(
                instrument_id=k[0],
                position_identifier=k[1],
                opening_lots=start,
                recorded_movement_lots=change,
                expected_lots=expected,
                observed_lots=actual,
                difference_lots=actual - expected,
                fill_count=counts.get(k, 0),
            )
        )
    return tuple(rows)


def compare_positions(
    store, connection, declaration: DeclaredOpeningPositions
) -> PositionComparison:
    opening_ref = sha256(canonical(declaration.model_dump(mode="json")).encode()).hexdigest()
    metadata = {"opening_ref": opening_ref}
    try:
        view, versions = store.position_history(connection)
        if view is None:
            raise ProjectionRejected("NO_HISTORY")
        metadata.update(
            source_receipt_ref=view.latest_receipt_ref,
            account_scope_ref=view.batch.account_scope_ref,
            history_start=view.history_start,
            history_through=view.history_through,
            position_observed_at=view.batch.observed_at,
            revised_deals=view.revision_count,
            cancelled_deals=sum(
                v.kind == "CASH" and v.payload.kind in {"BUY_CANCELED", "SELL_CANCELED"}
                for v in versions
            ),
        )
        if (
            declaration.account_scope_ref != view.batch.account_scope_ref
            or declaration.history_start != view.history_start
        ):
            raise ProjectionRejected("OPENING_MISMATCH")
        if view.missing_prior_deals:
            raise ProjectionRejected("MISSING_PRIOR_DEALS")
        if view.batch.history_end != view.history_through:
            raise ProjectionRejected("HISTORY_ENDPOINT")
        with localcontext(Context(prec=64)):
            rows = _rows(view, versions, declaration, {s.instrument_id for s in connection.symbols})
        return PositionComparison(
            state="DIVERGENT"
            if any(row.difference_lots != 0 for row in rows)
            else "CONSISTENT_WITH_DECLARATION",
            rows=rows,
            **metadata,
        )
    except ProjectionRejected as exc:
        code = (
            "SOURCE_INVALID"
            if exc.code in {"FOREIGN_SCOPE", "HISTORY_GAP", "STALE_OBSERVATION"}
            else exc.code
        )
        return PositionComparison(state="WITHHELD", code=code, **metadata)
    except Exception:
        return PositionComparison(state="WITHHELD", code="STORAGE_UNAVAILABLE", **metadata)
