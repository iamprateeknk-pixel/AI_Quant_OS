"""Predeclared hypotheses, bounded plans and chronological development partitions."""

from dataclasses import dataclass
from datetime import datetime, timezone
import re

from ai_quant_os.backtest import BacktestCosts
from ai_quant_os.market_data import BarDataset
from ai_quant_os.strategy_contracts import StrategyGenome
from ai_quant_os.contracts import _require_aware
from ai_quant_os.monte_carlo import MonteCarloSpec
from ai_quant_os.adversarial import AdversarialSpec


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{0,63}", value):
        raise ValueError("Invalid research identifier")


@dataclass(frozen=True)
class Hypothesis:
    hypothesis_id: str
    description: str

    def __post_init__(self):
        _identifier(self.hypothesis_id)
        if not isinstance(self.description, str) or not 1 <= len(self.description.strip()) <= 4096:
            raise ValueError("A bounded research hypothesis is required")


@dataclass(frozen=True)
class ResearchDatasetScope:
    instrument_id: str
    first_observed_at: datetime
    last_observed_at: datetime
    dataset_fingerprint: str

    def __post_init__(self):
        _identifier(self.instrument_id)
        for name in ("first_observed_at", "last_observed_at"):
            value = getattr(self, name)
            _require_aware(value)
            object.__setattr__(self, name, value.astimezone(timezone.utc))
        if (
            self.first_observed_at > self.last_observed_at
            or not isinstance(self.dataset_fingerprint, str)
            or not re.fullmatch(r"[a-f0-9]{64}", self.dataset_fingerprint)
        ):
            raise ValueError("Invalid holdout resource scope")


@dataclass(frozen=True)
class ResearchPlan:
    experiment_id: str
    hypothesis: Hypothesis
    candidates: tuple[StrategyGenome, ...]
    development_fingerprint: str
    development_scope: ResearchDatasetScope
    holdout_fingerprint: str
    holdout_scope: ResearchDatasetScope
    train_bars: int
    validation_bars: int
    quantity: int
    costs: BacktestCosts
    stress_costs: BacktestCosts
    schema_version: str = "1.0"
    selection_objective: str = "NET_PNL_THEN_GENOME_SHA256"
    partition_model: str = "EXPANDING_COLD_START_V1"
    monte_carlo_spec: MonteCarloSpec | None = None
    adversarial_spec: AdversarialSpec | None = None

    def __post_init__(self):
        _identifier(self.experiment_id)
        if (
            type(self.hypothesis) is not Hypothesis
            or type(self.candidates) is not tuple
            or not 1 <= len(self.candidates) <= 16
            or any(
                type(row) is not StrategyGenome or row.input_kind != "BAR"
                for row in self.candidates
            )
            or len({row.fingerprint for row in self.candidates}) != len(self.candidates)
        ):
            raise ValueError("A unique bounded bar candidate set is required")
        object.__setattr__(
            self, "candidates", tuple(sorted(self.candidates, key=lambda row: row.fingerprint))
        )
        for value in (self.development_fingerprint, self.holdout_fingerprint):
            if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
                raise ValueError("Research dataset fingerprint required")
        if self.development_fingerprint == self.holdout_fingerprint:
            raise ValueError("Development and holdout must be separated")
        if (
            type(self.development_scope) is not ResearchDatasetScope
            or self.development_scope.dataset_fingerprint != self.development_fingerprint
        ):
            raise ValueError("Development scope fingerprint mismatch")
        if (
            type(self.holdout_scope) is not ResearchDatasetScope
            or self.holdout_scope.dataset_fingerprint != self.holdout_fingerprint
        ):
            raise ValueError("Holdout scope fingerprint mismatch")
        if (
            self.development_scope.instrument_id != self.holdout_scope.instrument_id
            or self.development_scope.last_observed_at >= self.holdout_scope.first_observed_at
        ):
            raise ValueError("Development and holdout scopes must be separated")
        if any(
            type(value) is not int or not 2 <= value <= 2048
            for value in (self.train_bars, self.validation_bars)
        ):
            raise ValueError("Invalid research partition sizes")
        if type(self.quantity) is not int or not 1 <= self.quantity <= 100000:
            raise ValueError("Invalid research trade quantity")
        if (
            self.stress_costs.slippage_bps < self.costs.slippage_bps
            or self.stress_costs.fee_per_trade < self.costs.fee_per_trade
            or self.stress_costs == self.costs
        ):
            raise ValueError("Cost stress must be strictly adverse")
        if (
            self.schema_version not in ("1.0", "1.1", "1.2", "1.3")
            or self.selection_objective != "NET_PNL_THEN_GENOME_SHA256"
            or self.partition_model != "EXPANDING_COLD_START_V1"
        ):
            raise ValueError("Unsupported research plan contract")
        if (self.schema_version == "1.0" and self.monte_carlo_spec is not None) or (
            self.schema_version in ("1.1", "1.2", "1.3")
            and type(self.monte_carlo_spec) is not MonteCarloSpec
        ):
            raise ValueError("Research version requires its declared Monte Carlo policy")
        if (self.schema_version not in ("1.2", "1.3") and self.adversarial_spec is not None) or (
            self.schema_version in ("1.2", "1.3")
            and type(self.adversarial_spec) is not AdversarialSpec
        ):
            raise ValueError("Research version requires its declared adversarial policy")


def partition_development(dataset: BarDataset, *, train_bars: int, validation_bars: int):
    if (
        type(train_bars) is not int
        or type(validation_bars) is not int
        or min(train_bars, validation_bars) < 2
        or not 1 <= len(dataset.bars) <= 4096
        or len(dataset.bars) <= train_bars
        or (len(dataset.bars) - train_bars) % validation_bars
    ):
        raise ValueError("Development rows must form complete chronological validation folds")
    folds = tuple(
        (BarDataset(dataset.bars[:end]), BarDataset(dataset.bars[end : end + validation_bars]))
        for end in range(train_bars, len(dataset.bars), validation_bars)
    )
    if len(folds) > 16:
        raise ValueError("Research fold capacity exceeded")
    return folds
