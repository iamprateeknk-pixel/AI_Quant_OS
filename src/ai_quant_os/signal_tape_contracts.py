"""Unattested target values and native replay evidence; never a candidate certificate."""

import base64
from decimal import Context, Decimal, localcontext
from hashlib import sha256
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.market_data import BarDataset
from ai_quant_os.research_data_parsing import parse_research_csv
from ai_quant_os.validation_package_contracts import ValidationPackage


class SignalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    execute_at: AwareDatetime
    history_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    target: int = Field(strict=True, ge=-1, le=1)

    @model_validator(mode="after")
    def utc_clock(self):
        if self.execute_at.utcoffset().total_seconds() != 0:
            raise ValueError("Signal execution timestamp must be UTC")
        return self


class SignalTape(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    package_ref: str = Field(pattern=r"^validation-package:[a-f0-9]{64}$")
    candidate_ref: str = Field(pattern=r"^strategy-candidate:[a-f0-9]{64}$")
    dataset_ref: str = Field(pattern=r"^research-data:[a-f0-9]{64}$")
    dataset_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    decisions: tuple[SignalDecision, ...] = Field(min_length=2, max_length=4096)
    provenance: Literal["OPERATOR_SUPPLIED_UNATTESTED_TARGETS"] = (
        "OPERATOR_SUPPLIED_UNATTESTED_TARGETS"
    )
    source_execution_status: Literal["UNATTESTED"] = "UNATTESTED"
    execution_timing: Literal["NEXT_BAR_OPEN_AFTER_PREVIOUS_CLOSE_SIGNAL"] = (
        "NEXT_BAR_OPEN_AFTER_PREVIOUS_CLOSE_SIGNAL"
    )
    isolation_verified: Literal[False] = False
    promotion_authorized: Literal[False] = False

    @property
    def tape_ref(self) -> str:
        return "signal-tape:" + sha256(self.model_dump_json().encode()).hexdigest()

    def require_package(self, package: ValidationPackage) -> BarDataset:
        if (self.package_ref, self.candidate_ref, self.dataset_ref, self.dataset_fingerprint) != (
            package.package_ref,
            package.candidate.candidate_ref,
            package.holdout.receipt.dataset_ref,
            package.holdout.receipt.dataset_fingerprint,
        ):
            raise ValueError("Signal tape package/dataset binding mismatch")
        dataset = parse_research_csv(base64.b64decode(package.holdout.raw_base64, validate=True))
        if len(self.decisions) != len(dataset.bars):
            raise ValueError("Signal tape must cover every holdout open")
        for index, (decision, bar) in enumerate(zip(self.decisions, dataset.bars, strict=True)):
            with localcontext(Context(prec=28)):
                history = BarDataset(dataset.bars[max(0, index - 2) : index]).fingerprint
            if (
                decision.execute_at != bar.observed_at
                or decision.history_sha256 != history
                or (index < 2 and decision.target != 0)
            ):
                raise ValueError(
                    "Signal tape differs from closed-history/open-time/warmup contract"
                )
        return dataset


class SignalReplayFill(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    observed_at: AwareDatetime
    side: Literal["BUY", "SELL"]
    price: Decimal = Field(gt=0, allow_inf_nan=False)
    quantity: Decimal = Field(gt=0, le=1000000, allow_inf_nan=False)
    fee: Decimal = Field(ge=0, allow_inf_nan=False)


class SignalReplayCosts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    spread_bps: Decimal = Field(ge=0, allow_inf_nan=False)
    slippage_bps: Decimal = Field(ge=0, allow_inf_nan=False)
    commission_bps: Decimal = Field(ge=0, allow_inf_nan=False)
    multiplier: Decimal = Field(ge=1, allow_inf_nan=False)
    basis: Literal["OPERATOR_DECLARED_SYNTHETIC"] = "OPERATOR_DECLARED_SYNTHETIC"
    fee_model: Literal["NATIVE_TAKER_NOTIONAL_QUOTE_CURRENCY"] = (
        "NATIVE_TAKER_NOTIONAL_QUOTE_CURRENCY"
    )


class SignalReplayReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    status: Literal["UNATTESTED_NATIVE_REPLAY"] = "UNATTESTED_NATIVE_REPLAY"
    venue: Literal["PAPER"] = "PAPER"
    engine: Literal["NAUTILUS_BACKTEST"] = "NAUTILUS_BACKTEST"
    engine_version: Literal["1.221.0"] = "1.221.0"
    adapter_ref: Literal["precomputed-signal-tape@0.1.0"] = "precomputed-signal-tape@0.1.0"
    package_ref: str = Field(pattern=r"^validation-package:[a-f0-9]{64}$")
    tape: SignalTape
    instrument_id: Literal["EURUSD", "GBPUSD", "USDJPY", "EURJPY"]
    pnl_currency: Literal["USD", "JPY"]
    account_currency_basis: Literal["INSTRUMENT_QUOTE_ONLY"] = "INSTRUMENT_QUOTE_ONLY"
    bar_execution_model: Literal["SYNTHETIC_OPEN_CLOSE_QUOTES_NEXT_OPEN_TARGETS"] = (
        "SYNTHETIC_OPEN_CLOSE_QUOTES_NEXT_OPEN_TARGETS"
    )
    synthetic_quote_size: Literal[1000000] = 1000000
    liquidity_basis: Literal["FIXED_SYNTHETIC_SIZE_NOT_SOURCE_VOLUME_OR_BROKER_DEPTH"] = (
        "FIXED_SYNTHETIC_SIZE_NOT_SOURCE_VOLUME_OR_BROKER_DEPTH"
    )
    collateral_model: Literal["FULL_TARGET_ASK_NOTIONAL_QUOTE_EQUITY_V1"] = (
        "FULL_TARGET_ASK_NOTIONAL_QUOTE_EQUITY_V1"
    )
    cost_scenario: Literal["BASE", "DECLARED_STRESS"]
    costs: SignalReplayCosts
    quantity: int = Field(strict=True, ge=1, le=500000)
    initial_balance: Decimal = Field(gt=0, le=1000000000000, allow_inf_nan=False)
    final_mark: Decimal = Field(gt=0, allow_inf_nan=False)
    fills: tuple[SignalReplayFill, ...] = Field(max_length=4096)
    native_order_count: int = Field(strict=True, ge=0, le=4096)
    final_position: Decimal = Field(allow_inf_nan=False)
    net_pnl: Decimal = Field(allow_inf_nan=False)
    native_realized_pnl: Decimal = Field(allow_inf_nan=False)
    native_realized_pnl_verification: Literal["NATIVE_CACHE_VALUE_NOT_INDEPENDENTLY_CHECKED"] = (
        "NATIVE_CACHE_VALUE_NOT_INDEPENDENTLY_CHECKED"
    )
    pnl_method: Literal["FILL_CASHFLOWS_PLUS_FINAL_CLOSE_MARK_NOT_LIQUIDATION"] = (
        "FILL_CASHFLOWS_PLUS_FINAL_CLOSE_MARK_NOT_LIQUIDATION"
    )
    source_execution_status: Literal["UNATTESTED"] = "UNATTESTED"
    candidate_code_executed: Literal[False] = False
    candidate_tests_status: Literal["NOT_RUN"] = "NOT_RUN"
    isolation_verified: Literal[False] = False
    execution_authorized: Literal[False] = False
    promotion_authorized: Literal[False] = False
    financing_costs: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    account_currency_conversion: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    alpha_acceptance: Literal["UNASSESSED"] = "UNASSESSED"

    @model_validator(mode="after")
    def native_ledger(self):
        if self.package_ref != self.tape.package_ref or self.native_order_count != len(self.fills):
            raise ValueError("Signal replay input/order binding mismatch")
        with localcontext(Context(prec=28)):
            position, cash = Decimal(0), Decimal(0)
            previous = None
            for fill in self.fills:
                if previous is not None and fill.observed_at <= previous:
                    raise ValueError("Signal replay fills must have increasing execution times")
                signed = fill.quantity if fill.side == "BUY" else -fill.quantity
                position += signed
                cash -= signed * fill.price + fill.fee
                if position not in (-Decimal(self.quantity), Decimal(0), Decimal(self.quantity)):
                    raise ValueError("Signal replay target position mismatch")
                previous = fill.observed_at
            if position != self.final_position or cash + position * self.final_mark != self.net_pnl:
                raise ValueError("Signal replay cash-flow mismatch")
        return self
