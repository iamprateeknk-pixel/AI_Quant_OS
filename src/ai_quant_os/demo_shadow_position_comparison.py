"""Compare exact-time PAPER shadow and unverified demo position directions."""

from decimal import Decimal
from typing import Literal

from ai_quant_os.mt5_portfolio_observation import DemoPortfolioRecord
from ai_quant_os.mt5_account_sync import PositionView
from ai_quant_os.mt5_demo_observation import ObservationModel
from ai_quant_os.mt5_demo_gateway import utc
from ai_quant_os.shadow_portfolio import ShadowReport
from pydantic import AwareDatetime, Field, model_validator


class DemoShadowPositionComparison(ObservationModel):
    state: Literal[
        "SAME_DIRECTION",
        "OPPOSITE_DIRECTION",
        "SHADOW_ONLY",
        "DEMO_ONLY",
        "BOTH_FLAT",
        "DEMO_HEDGED_NEUTRAL",
        "WITHHELD",
    ]
    code: Literal["SOURCE_TIME_MISMATCH", "HISTORY_WINDOW_MISMATCH"] | None
    broker_id: Literal["EXNESS_MT5_DEMO"]
    mode: Literal["DEMO_OBSERVATION"]
    shadow_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    shadow_sequence: int = Field(strict=True, ge=1, le=1000)
    candidate_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_report_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    genome_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    instrument_id: str = Field(min_length=1, max_length=64)
    source_time: AwareDatetime
    shadow_interval_start: AwareDatetime
    shadow_interval_end: AwareDatetime
    demo_observed_at: AwareDatetime
    demo_history_start: AwareDatetime
    demo_history_through: AwareDatetime
    shadow_source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    demo_source_receipt_ref: str = Field(pattern=r"^[a-f0-9]{64}$")
    demo_account_scope_ref: str = Field(pattern=r"^[a-f0-9]{64}$")
    shadow_position_strategy_units: Decimal | None = Field(
        default=None,
        allow_inf_nan=False,
        description="Signed abstract position in strategy-model units; not convertible to broker quantity without an explicit size binding.",
    )
    demo_position_current_contract_units: Decimal | None = Field(
        default=None,
        allow_inf_nan=False,
        description="Signed observed broker quantity in units derived from current symbol contract metadata.",
    )
    shadow_quantity_basis: Literal["STRATEGY_MODEL_UNITS"]
    demo_quantity_basis: Literal["CURRENT_SYMBOL_METADATA"]
    demo_has_instrument_positions: bool | None
    magnitude_comparison: Literal["NOT_AVAILABLE"] = Field(
        description="Strategy-model and broker quantities have incompatible units; no magnitude comparison is made."
    )
    demo_attestation: Literal["UNVERIFIED"]
    consistency: Literal["NON_ATOMIC_SEQUENTIAL_READ"]
    causal_attribution: Literal[False]
    promotion_allowed: Literal[False]
    may_submit_orders: Literal[False]
    may_affect_portfolio: Literal[False]
    schema_version: Literal["1.0"] = "1.0"

    @model_validator(mode="after")
    def valid_direction_comparison(self):
        quantities = (
            self.shadow_position_strategy_units,
            self.demo_position_current_contract_units,
        )
        if self.state == "WITHHELD":
            if self.code not in {"SOURCE_TIME_MISMATCH", "HISTORY_WINDOW_MISMATCH"} or any(
                value is not None for value in quantities
            ):
                raise ValueError("Withheld demo/shadow comparison must omit position values")
            return self
        if (
            self.code is not None
            or any(value is None for value in quantities)
            or self.source_time != self.demo_observed_at
            or self.shadow_interval_end != self.source_time
            or self.demo_history_start > self.shadow_interval_start
            or self.demo_history_through < self.shadow_interval_end
        ):
            raise ValueError("Inconsistent demo/shadow direction comparison")
        shadow_sign = (self.shadow_position_strategy_units > 0) - (
            self.shadow_position_strategy_units < 0
        )
        demo_sign = (self.demo_position_current_contract_units > 0) - (
            self.demo_position_current_contract_units < 0
        )
        expected = (
            "DEMO_HEDGED_NEUTRAL"
            if self.demo_has_instrument_positions and demo_sign == 0
            else {
                (0, 0): "BOTH_FLAT",
                (0, 1): "DEMO_ONLY",
                (0, -1): "DEMO_ONLY",
                (1, 0): "SHADOW_ONLY",
                (-1, 0): "SHADOW_ONLY",
                (1, 1): "SAME_DIRECTION",
                (-1, -1): "SAME_DIRECTION",
                (1, -1): "OPPOSITE_DIRECTION",
                (-1, 1): "OPPOSITE_DIRECTION",
            }[(shadow_sign, demo_sign)]
        )
        if self.state != expected:
            raise ValueError("State does not match observed position directions")
        return self


def compare_demo_shadow_position(
    demo: DemoPortfolioRecord, shadow: ShadowReport
) -> DemoShadowPositionComparison:
    if type(demo) is not DemoPortfolioRecord or type(shadow) is not ShadowReport:
        raise ValueError("Typed demo and retained shadow observations are required")
    if shadow.outcome != "OBSERVED" or shadow.run is None:
        raise ValueError("A completed observed shadow report is required")

    instrument_id = shadow.run.result.instrument_id
    source_time = shadow.source_bars[-1].observed_at
    instrument_positions = tuple(
        position for position in demo.positions if position.instrument_id == instrument_id
    )
    interval_start = shadow.source_bars[0].observed_at
    interval_end = shadow.source_bars[-1].observed_at
    identity = {
        "broker_id": demo.broker_id,
        "mode": "DEMO_OBSERVATION",
        "shadow_id": shadow.assignment.shadow_id,
        "shadow_sequence": shadow.sequence,
        "candidate_id": shadow.assignment.candidate.manifest.candidate_id,
        "candidate_report_sha256": shadow.assignment.candidate.manifest.report_sha256,
        "genome_sha256": shadow.assignment.candidate.report.holdout.binding.genome.fingerprint,
        "instrument_id": instrument_id,
        "source_time": source_time,
        "shadow_interval_start": interval_start,
        "shadow_interval_end": interval_end,
        "demo_observed_at": demo.observed_at,
        "demo_history_start": demo.history_start,
        "demo_history_through": demo.history_through,
        "shadow_source_sha256": shadow.fingerprint,
        "demo_source_receipt_ref": demo.source_receipt_ref,
        "demo_account_scope_ref": demo.account_scope_ref,
        "demo_has_instrument_positions": bool(instrument_positions),
        "shadow_quantity_basis": "STRATEGY_MODEL_UNITS",
        "demo_quantity_basis": "CURRENT_SYMBOL_METADATA",
        "magnitude_comparison": "NOT_AVAILABLE",
        "demo_attestation": "UNVERIFIED",
        "consistency": "NON_ATOMIC_SEQUENTIAL_READ",
        "causal_attribution": False,
        "promotion_allowed": False,
        "may_submit_orders": False,
        "may_affect_portfolio": False,
    }
    if demo.observed_at != source_time:
        return DemoShadowPositionComparison(
            state="WITHHELD",
            code="SOURCE_TIME_MISMATCH",
            shadow_position_strategy_units=None,
            demo_position_current_contract_units=None,
            **identity,
        )
    if demo.history_start > interval_start or demo.history_through < interval_end:
        return DemoShadowPositionComparison(
            state="WITHHELD",
            code="HISTORY_WINDOW_MISMATCH",
            shadow_position_strategy_units=None,
            demo_position_current_contract_units=None,
            **identity,
        )
    demo_quantity = sum(
        (
            position.quantity_units if position.side == "BUY" else -position.quantity_units
            for position in instrument_positions
        ),
        Decimal(0),
    )
    shadow_quantity = Decimal(shadow.run.result.final_position)
    shadow_sign = (shadow_quantity > 0) - (shadow_quantity < 0)
    demo_sign = (demo_quantity > 0) - (demo_quantity < 0)
    state = (
        "DEMO_HEDGED_NEUTRAL"
        if instrument_positions and demo_sign == 0
        else {
            (0, 0): "BOTH_FLAT",
            (0, 1): "DEMO_ONLY",
            (0, -1): "DEMO_ONLY",
            (1, 0): "SHADOW_ONLY",
            (-1, 0): "SHADOW_ONLY",
            (1, 1): "SAME_DIRECTION",
            (-1, -1): "SAME_DIRECTION",
            (1, -1): "OPPOSITE_DIRECTION",
            (-1, 1): "OPPOSITE_DIRECTION",
        }[(shadow_sign, demo_sign)]
    )
    return DemoShadowPositionComparison(
        state=state,
        code=None,
        shadow_position_strategy_units=shadow_quantity,
        demo_position_current_contract_units=demo_quantity,
        **identity,
    )


class DemoPositionSnapshot(ObservationModel):
    account_scope_ref: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_receipt_ref: str = Field(pattern=r"^[a-f0-9]{64}$")
    observed_at: AwareDatetime
    history_start: AwareDatetime
    history_through: AwareDatetime
    positions: tuple[PositionView, ...] = Field(max_length=8)

    @model_validator(mode="after")
    def valid_history_window(self):
        if not self.history_start < self.history_through <= self.observed_at:
            raise ValueError("Invalid demo position snapshot history window")
        return self


class DemoShadowPositionObservation(ObservationModel):
    observed_at: AwareDatetime
    shadow_direction: Literal["LONG", "SHORT", "FLAT"]
    demo_direction: Literal["LONG", "SHORT", "FLAT"] | None
    demo_has_instrument_positions: bool | None
    state: Literal[
        "SAME_DIRECTION",
        "OPPOSITE_DIRECTION",
        "SHADOW_ONLY",
        "DEMO_ONLY",
        "BOTH_FLAT",
        "DEMO_HEDGED_NEUTRAL",
        "WITHHELD",
    ]
    code: Literal["NO_EXACT_DEMO_SNAPSHOT"] | None
    demo_source_receipt_ref: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def direction_matches_state(self):
        if self.code is not None:
            if (
                self.code != "NO_EXACT_DEMO_SNAPSHOT"
                or self.state != "WITHHELD"
                or self.demo_direction is not None
                or self.demo_has_instrument_positions is not None
                or self.demo_source_receipt_ref is not None
            ):
                raise ValueError("Unmatched demo position must remain withheld")
            return self
        if (
            self.state == "WITHHELD"
            or self.demo_direction is None
            or self.demo_has_instrument_positions is None
            or self.demo_source_receipt_ref is None
        ):
            raise ValueError("Matched demo position requires complete evidence")
        expected = _direction_state(
            self.shadow_direction,
            self.demo_direction,
            self.demo_has_instrument_positions,
        )
        if self.state != expected:
            raise ValueError("Position directions disagree with comparison state")
        return self


class DemoShadowPositionHistoryComparison(ObservationModel):
    schema_version: Literal["1.0"] = "1.0"
    observation_kind: Literal["DEMO_SHADOW_POSITION_HISTORY_COMPARISON"] = (
        "DEMO_SHADOW_POSITION_HISTORY_COMPARISON"
    )
    coverage: Literal["COMPLETE", "PARTIAL", "NONE"]
    shadow_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    shadow_sequence: int = Field(strict=True, ge=1, le=1000)
    candidate_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_report_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    genome_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    instrument_id: str = Field(min_length=1, max_length=64)
    shadow_source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    shadow_interval_start: AwareDatetime
    shadow_interval_end: AwareDatetime
    demo_account_scope_ref: str = Field(pattern=r"^[a-f0-9]{64}$")
    demo_history_start: AwareDatetime
    demo_history_through: AwareDatetime
    total_source_bars: int = Field(ge=1, le=4096, strict=True)
    matched_source_bars: int = Field(ge=0, le=4096, strict=True)
    observations: tuple[DemoShadowPositionObservation, ...] = Field(min_length=1, max_length=4096)
    shadow_quantity_basis: Literal["STRATEGY_MODEL_UNITS"] = "STRATEGY_MODEL_UNITS"
    demo_quantity_basis: Literal["CURRENT_SYMBOL_METADATA"] = "CURRENT_SYMBOL_METADATA"
    magnitude_comparison: Literal["NOT_AVAILABLE"] = "NOT_AVAILABLE"
    demo_attestation: Literal["UNVERIFIED"] = "UNVERIFIED"
    history_attestation: Literal["UNVERIFIED"] = "UNVERIFIED"
    consistency: Literal["NON_ATOMIC_SEQUENTIAL_READ"] = "NON_ATOMIC_SEQUENTIAL_READ"
    causal_attribution: Literal[False] = False
    promotion_allowed: Literal[False] = False
    may_submit_orders: Literal[False] = False
    may_affect_portfolio: Literal[False] = False

    @model_validator(mode="after")
    def coverage_matches_observations(self):
        matched = sum(row.code is None for row in self.observations)
        expected = (
            "COMPLETE"
            if matched == self.total_source_bars
            else "NONE"
            if matched == 0
            else "PARTIAL"
        )
        if (
            len(self.observations) != self.total_source_bars
            or matched != self.matched_source_bars
            or self.coverage != expected
            or tuple(row.observed_at for row in self.observations)
            != tuple(sorted({row.observed_at for row in self.observations}))
            or self.observations[0].observed_at != self.shadow_interval_start
            or self.observations[-1].observed_at != self.shadow_interval_end
        ):
            raise ValueError("Shadow position history coverage does not reconcile")
        return self


def _direction(quantity: Decimal) -> Literal["LONG", "SHORT", "FLAT"]:
    return "LONG" if quantity > 0 else "SHORT" if quantity < 0 else "FLAT"


def _direction_state(shadow_direction, demo_direction, demo_has_positions):
    if demo_has_positions and demo_direction == "FLAT":
        return "DEMO_HEDGED_NEUTRAL"
    return {
        ("FLAT", "FLAT"): "BOTH_FLAT",
        ("FLAT", "LONG"): "DEMO_ONLY",
        ("FLAT", "SHORT"): "DEMO_ONLY",
        ("LONG", "FLAT"): "SHADOW_ONLY",
        ("SHORT", "FLAT"): "SHADOW_ONLY",
        ("LONG", "LONG"): "SAME_DIRECTION",
        ("SHORT", "SHORT"): "SAME_DIRECTION",
        ("LONG", "SHORT"): "OPPOSITE_DIRECTION",
        ("SHORT", "LONG"): "OPPOSITE_DIRECTION",
    }[(shadow_direction, demo_direction)]


def compare_demo_shadow_position_history(
    snapshots: tuple[DemoPositionSnapshot, ...], shadow: ShadowReport
) -> DemoShadowPositionHistoryComparison:
    if (
        type(snapshots) is not tuple
        or not snapshots
        or len(snapshots) > 1000
        or any(type(snapshot) is not DemoPositionSnapshot for snapshot in snapshots)
        or type(shadow) is not ShadowReport
        or shadow.outcome != "OBSERVED"
        or shadow.run is None
    ):
        raise ValueError("Verified bounded demo snapshots and observed shadow report required")
    ordered = tuple(sorted(snapshots, key=lambda row: row.observed_at))
    if (
        len({row.observed_at for row in ordered}) != len(ordered)
        or len({row.account_scope_ref for row in ordered}) != 1
    ):
        raise ValueError("Demo snapshot timestamps and account scope must be unique")

    instrument_id = shadow.run.result.instrument_id
    by_time = {row.observed_at: row for row in ordered}
    fills = shadow.run.result.fills
    observations = []
    for bar in shadow.source_bars:
        shadow_quantity = sum(
            (
                fill.quantity if fill.side == "BUY" else -fill.quantity
                for fill in fills
                if utc(fill.observed_at) <= utc(bar.observed_at)
            ),
            Decimal(0),
        )
        shadow_direction = _direction(shadow_quantity)
        demo = by_time.get(bar.observed_at)
        if demo is None:
            observations.append(
                DemoShadowPositionObservation(
                    observed_at=bar.observed_at,
                    shadow_direction=shadow_direction,
                    demo_direction=None,
                    demo_has_instrument_positions=None,
                    state="WITHHELD",
                    code="NO_EXACT_DEMO_SNAPSHOT",
                )
            )
            continue
        positions = tuple(row for row in demo.positions if row.instrument_id == instrument_id)
        demo_quantity = sum(
            (row.quantity_units if row.side == "BUY" else -row.quantity_units for row in positions),
            Decimal(0),
        )
        demo_direction = _direction(demo_quantity)
        observations.append(
            DemoShadowPositionObservation(
                observed_at=bar.observed_at,
                shadow_direction=shadow_direction,
                demo_direction=demo_direction,
                demo_has_instrument_positions=bool(positions),
                state=_direction_state(shadow_direction, demo_direction, bool(positions)),
                code=None,
                demo_source_receipt_ref=demo.source_receipt_ref,
            )
        )

    matched = sum(row.code is None for row in observations)
    coverage = "COMPLETE" if matched == len(observations) else "NONE" if matched == 0 else "PARTIAL"
    return DemoShadowPositionHistoryComparison(
        coverage=coverage,
        shadow_id=shadow.assignment.shadow_id,
        shadow_sequence=shadow.sequence,
        candidate_id=shadow.assignment.candidate.manifest.candidate_id,
        candidate_report_sha256=shadow.assignment.candidate.manifest.report_sha256,
        genome_sha256=shadow.assignment.candidate.report.holdout.binding.genome.fingerprint,
        instrument_id=instrument_id,
        shadow_source_sha256=shadow.fingerprint,
        shadow_interval_start=shadow.source_bars[0].observed_at,
        shadow_interval_end=shadow.source_bars[-1].observed_at,
        demo_account_scope_ref=ordered[0].account_scope_ref,
        demo_history_start=min(row.history_start for row in ordered),
        demo_history_through=max(row.history_through for row in ordered),
        total_source_bars=len(observations),
        matched_source_bars=matched,
        observations=tuple(observations),
    )
