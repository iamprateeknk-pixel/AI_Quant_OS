"""Declared alternatives for diagnostics on an already observed candidate holdout."""

from dataclasses import dataclass
import re

from ai_quant_os.backtest import BacktestCosts
from ai_quant_os.strategy_contracts import StrategyGenome


@dataclass(frozen=True)
class ReplayVariant:
    name: str
    genome: StrategyGenome
    quantity: int
    costs: BacktestCosts

    def __post_init__(self):
        if not isinstance(self.name, str) or not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_-]{0,63}", self.name
        ):
            raise ValueError("Invalid variant name")
        if type(self.genome) is not StrategyGenome or self.genome.input_kind != "BAR":
            raise ValueError("Counterfactual requires a declared bar genome")
        if type(self.quantity) is not int or not 1 <= self.quantity <= 100000:
            raise ValueError("Invalid counterfactual quantity")
        if type(self.costs) is not BacktestCosts:
            raise ValueError("Declared counterfactual costs required")

    @property
    def configuration(self):
        return self.genome, self.quantity, self.costs


@dataclass(frozen=True)
class CounterfactualPlan:
    replay_id: str
    candidate_id: str
    candidate_report_sha256: str
    dataset_fingerprint: str
    variants: tuple[ReplayVariant, ...]
    schema_version: str = "1.0"
    purpose: str = "POST_VALIDATION_COUNTERFACTUAL_DIAGNOSTIC"

    def __post_init__(self):
        if not isinstance(self.replay_id, str) or not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_-]{0,63}", self.replay_id
        ):
            raise ValueError("Invalid counterfactual replay identity")
        for digest in (self.candidate_id, self.candidate_report_sha256, self.dataset_fingerprint):
            if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
                raise ValueError("Counterfactual fingerprint required")
        if (
            self.schema_version != "1.0"
            or self.purpose != "POST_VALIDATION_COUNTERFACTUAL_DIAGNOSTIC"
        ):
            raise ValueError("Counterfactual diagnostics cannot become validation")
        if (
            type(self.variants) is not tuple
            or not 1 <= len(self.variants) <= 7
            or any(
                type(row) is not ReplayVariant or row.name.upper() == "BASELINE"
                for row in self.variants
            )
            or len({row.name for row in self.variants}) != len(self.variants)
            or len({row.configuration for row in self.variants}) != len(self.variants)
        ):
            raise ValueError("A unique bounded alternative set is required")
