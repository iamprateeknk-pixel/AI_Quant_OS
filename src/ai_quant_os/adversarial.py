"""Declared, deterministic bar perturbations; no strategy selection or execution."""

from dataclasses import dataclass, replace
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext

from ai_quant_os.market_data import BarDataset


@dataclass(frozen=True)
class AdversarialSpec:
    drop_every: int = 5
    shock_bps: int = 1000
    maximum_loss: Decimal = Decimal(100)
    risk_currency: str = "USD"
    method: str = "MISSING_AND_DOWNWARD_STEP_V1"

    def __post_init__(self):
        if (
            type(self.drop_every) is not int
            or not 2 <= self.drop_every <= 16
            or type(self.shock_bps) is not int
            or not 1 <= self.shock_bps <= 5000
            or type(self.maximum_loss) is not Decimal
            or not self.maximum_loss.is_finite()
            or not 0 <= self.maximum_loss <= Decimal("1e12")
            or self.risk_currency not in ("USD", "JPY")
            or self.method != "MISSING_AND_DOWNWARD_STEP_V1"
        ):
            raise ValueError("Invalid declared adversarial policy")


def adversarial_datasets(source: BarDataset, spec: AdversarialSpec):
    with localcontext(Context(prec=28)):
        if (
            not 2 * spec.drop_every <= len(source.bars) <= 4096
            or len({bar.instrument_id for bar in source.bars}) != 1
            or source.bars[0].instrument_id not in ("EURUSD", "GBPUSD", "USDJPY", "EURJPY")
        ):
            raise ValueError("Adversarial source requires bounded single-instrument observations")
        quantum = Decimal("0.001" if source.bars[0].instrument_id.endswith("JPY") else "0.00001")
        multiplier = Decimal(1) - Decimal(spec.shock_bps) / 10000
        missing = BarDataset(
            bar for index, bar in enumerate(source.bars, 1) if index % spec.drop_every
        )
        shocked = BarDataset(
            bar
            if index < len(source.bars) // 2
            else replace(
                bar,
                **{
                    field: (getattr(bar, field) * multiplier).quantize(
                        quantum, rounding=ROUND_HALF_EVEN
                    )
                    for field in ("open", "high", "low", "close")
                },
            )
            for index, bar in enumerate(source.bars)
        )
        if missing.fingerprint == source.fingerprint or shocked.fingerprint == source.fingerprint:
            raise ValueError("Adversarial perturbation must change the source")
        return (("MISSING_OBSERVATIONS", missing), ("DOWNWARD_STEP", shocked))
