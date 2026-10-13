"""Compare exact-time PAPER shadow and unverified demo position directions."""

from decimal import Decimal
from typing import Literal

from ai_quant_os.mt5_portfolio_observation import DemoPortfolioRecord
from ai_quant_os.mt5_demo_observation import ObservationModel
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
