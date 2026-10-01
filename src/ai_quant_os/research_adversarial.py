"""Native scenario evidence bound to the frozen candidate and original holdout bars."""

from dataclasses import dataclass
from decimal import Context, Decimal, localcontext
from typing import Literal

from ai_quant_os.adversarial import adversarial_datasets
from ai_quant_os.market_data import Bar, BarDataset
from ai_quant_os.registered_backtest import RegisteredBacktest


@dataclass(frozen=True)
class AdversarialScenario:
    name: str
    dataset_fingerprint: str
    run: RegisteredBacktest


@dataclass(frozen=True)
class NativeAdversarial:
    source_bars: tuple[Bar, ...]
    scenarios: tuple[AdversarialScenario, ...]
    state: Literal["PASS", "FAIL", "PENDING"]
    reason: str


def adversarial_score(scenarios, spec):
    with localcontext(Context(prec=28)):
        if any(row.run.result.pnl_currency != spec.risk_currency for row in scenarios):
            raise ValueError("Adversarial loss currency differs from native result")
        if any(row.run.result.net_pnl < -spec.maximum_loss for row in scenarios):
            return "FAIL", "DECLARED_SCENARIO_LOSS_EXCEEDED"
        if any(row.run.result.native_order_count == 0 for row in scenarios):
            return "PENDING", "SCENARIO_ACTIVITY_MISSING"
        return "PASS", "DECLARED_SCENARIO_SCREEN_PASSED"


def verify_adversarial(evidence, plan, baseline, attempts):
    with localcontext(Context(prec=28)):
        if evidence is None or not 1 <= len(evidence.source_bars) <= 4096:
            raise ValueError("Adversarial source evidence missing")
        source = BarDataset(evidence.source_bars)
        if source.bars != evidence.source_bars or source.fingerprint != plan.holdout_fingerprint:
            raise ValueError("Adversarial source differs from reserved holdout")
        expected = adversarial_datasets(source, plan.adversarial_spec)
        if len(evidence.scenarios) != len(expected):
            raise ValueError("Adversarial scenarios cannot be skipped")
        for row, (name, dataset) in zip(evidence.scenarios, expected, strict=True):
            if (
                row.name != name
                or row.dataset_fingerprint != dataset.fingerprint
                or row.run.result.dataset_fingerprint != dataset.fingerprint
                or row.run.binding != baseline.binding
                or row.run.result.costs != plan.stress_costs
                or row.run.result.instrument_id != baseline.result.instrument_id
                or row.run.result.strategy_ref != baseline.result.strategy_ref
                or row.run.result.replay_model != "BAR_OPEN_CLOSE_QUOTES_V1"
                or row.run not in attempts
            ):
                raise ValueError("Adversarial native evidence binding mismatch")
            position, cash = Decimal(0), Decimal(0)
            for fill in row.run.result.fills:
                if fill.quantity != plan.quantity or fill.fee != plan.stress_costs.fee_per_trade:
                    raise ValueError("Adversarial fill differs from declared quantity or fee")
                signed = fill.quantity if fill.side == "BUY" else -fill.quantity
                position += signed
                cash -= signed * fill.price + fill.fee
            if (
                position != row.run.result.final_position
                or position not in (Decimal(0), Decimal(plan.quantity))
                or len(row.run.result.fills) != row.run.result.native_order_count
                or cash + position * dataset.bars[-1].close != row.run.result.net_pnl
            ):
                raise ValueError("Adversarial cash-flow ledger differs from native result")
        if (evidence.state, evidence.reason) != adversarial_score(
            evidence.scenarios, plan.adversarial_spec
        ):
            raise ValueError("Adversarial score differs from native evidence")
