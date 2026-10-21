"""Deterministic, non-authoritative training-data measurements for P11."""

import json
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
from hashlib import sha256
import re
from typing import Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)

from ai_quant_os.agent_genomes import AgentGenome, require_genome_child
from ai_quant_os.evolution_protocols import (
    EvaluationProtocol,
    EvolutionDataset,
    require_protocol_genome,
)
from ai_quant_os.ingress_health import CODES

MAX_EVALUATION_OUTPUTS_BYTES = 4194304
WeaknessCategory = Literal[
    "REGIME_LABEL_MISMATCH",
    "REGIME_EVIDENCE_MISMATCH",
    "REGIME_DISTRIBUTION_MISMATCH",
    "INTEGRITY_STATUS_MISMATCH",
    "INSTRUMENT_FINDINGS_MISMATCH",
    "QUARANTINE_SET_MISMATCH",
    "INGRESS_COUNTS_MISMATCH",
    "CLOCK_EVIDENCE_MISMATCH",
    "CONTRADICTION_SET_MISMATCH",
    "OUTPUT_SHAPE_MISMATCH",
    "OTHER_VALUE_MISMATCH",
]


class CaseOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    output_json: str = Field(min_length=2, max_length=16384)

    @field_validator("output_json")
    @classmethod
    def canonical_object(cls, raw: str) -> str:
        try:
            parsed = json.loads(raw)
            canonical = json.dumps(parsed, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (json.JSONDecodeError, RecursionError, TypeError, ValueError):
            raise ValueError("Case output must be a canonical JSON object") from None
        if not isinstance(parsed, dict) or canonical != raw or len(raw.encode()) > 16384:
            raise ValueError("Case output must be a bounded canonical JSON object")
        return raw


class EvaluationOutputBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"]
    baseline_outputs: tuple[CaseOutput, ...] = Field(min_length=1, max_length=128)
    challenger_outputs: tuple[CaseOutput, ...] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def bounded_artifact(self):
        raw = self.model_dump_json().encode()
        if len(raw) > MAX_EVALUATION_OUTPUTS_BYTES:
            raise ValueError("Output bundle exceeds the evaluation input bound")
        return self


class MetricComparison(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metric: Literal["CORRECTNESS", "REGIME_CORRECTNESS", "INTEGRITY_CORRECTNESS"]
    baseline_score: Decimal = Field(ge=0, le=1, allow_inf_nan=False)
    challenger_score: Decimal = Field(ge=0, le=1, allow_inf_nan=False)
    case_count: int = Field(strict=True, ge=1, le=128)
    baseline_exact_matches: int = Field(strict=True, ge=0, le=128)
    challenger_exact_matches: int = Field(strict=True, ge=0, le=128)
    maximum_regression: Decimal = Field(ge=0, le=1, allow_inf_nan=False)
    within_guardrail: StrictBool

    @model_validator(mode="after")
    def checked_guardrail(self):
        if max(self.baseline_exact_matches, self.challenger_exact_matches) > self.case_count:
            raise ValueError("Exact match counts exceed the full case inventory")
        if self.baseline_score != Decimal(self.baseline_exact_matches) / Decimal(
            self.case_count
        ) or self.challenger_score != Decimal(self.challenger_exact_matches) / Decimal(
            self.case_count
        ):
            raise ValueError("Displayed scores differ from their exact match counts")
        exact_delta = Fraction(
            self.challenger_exact_matches - self.baseline_exact_matches, self.case_count
        )
        expected = exact_delta >= -Fraction(self.maximum_regression)
        if self.within_guardrail is not expected:
            raise ValueError("Guardrail result differs from measured scores")
        return self


class CaseDiagnostic(BaseModel):
    """Case-level correctness tags; source inputs and outputs are not retained."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    baseline_correct: StrictBool
    challenger_correct: StrictBool
    weakness_categories: tuple[WeaknessCategory, ...] | None = Field(default=None, max_length=8)

    @model_validator(mode="after")
    def category_inventory(self):
        if self.weakness_categories is not None and self.weakness_categories != tuple(
            sorted(set(self.weakness_categories))
        ):
            raise ValueError("Weakness categories must be sorted and unique")
        return self


class EvolutionScorecard(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0", "1.1", "1.2", "1.3"] = "1.3"
    scope: Literal["TRAINING_DIAGNOSTIC"] = "TRAINING_DIAGNOSTIC"
    diagnostic: Literal["IMPROVES_ON_TRAINING_DATA", "NO_IMPROVEMENT", "GUARDRAIL_REGRESSION"]
    dataset_ref: str = Field(pattern=r"^evolution-dataset:[a-f0-9]{64}$")
    dataset_provenance: Literal[
        "SYNTHETIC_DEVELOPMENT_NOT_HOLDOUT", "RECORDED_REFERENCE", "REVIEWED_REFERENCE"
    ]
    protocol_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    baseline_genome_ref: str = Field(pattern=r"^agent-genome:[a-f0-9]{64}$")
    challenger_genome_ref: str = Field(pattern=r"^agent-genome:[a-f0-9]{64}$")
    primary_metric: Literal["CORRECTNESS", "REGIME_CORRECTNESS", "INTEGRITY_CORRECTNESS"]
    baseline_score: Decimal = Field(ge=0, le=1, allow_inf_nan=False)
    challenger_score: Decimal = Field(ge=0, le=1, allow_inf_nan=False)
    case_count: int = Field(strict=True, ge=1, le=128)
    baseline_exact_matches: int = Field(strict=True, ge=0, le=128)
    challenger_exact_matches: int = Field(strict=True, ge=0, le=128)
    minimum_improvement: Decimal = Field(ge=0, le=1, allow_inf_nan=False)
    minimum_improvement_met: StrictBool
    metrics: tuple[MetricComparison, ...] = Field(min_length=1, max_length=8)
    case_diagnostics: tuple[CaseDiagnostic, ...] = Field(min_length=1, max_length=128)
    evaluated_at: AwareDatetime
    evaluation_verified: Literal[False] = False
    promotion_allowed: Literal[False] = False

    @field_validator("evaluation_verified", "promotion_allowed", mode="before")
    @classmethod
    def deny_authority(cls, value: object) -> bool:
        if value is not False:
            raise ValueError("Training scorecards cannot claim verification or promotion")
        return False

    @model_validator(mode="after")
    def scorecard_consistency(self):
        primary = next((row for row in self.metrics if row.metric == self.primary_metric), None)
        metric_names = tuple(row.metric for row in self.metrics)
        diagnostic_ids = tuple(row.case_id for row in self.case_diagnostics)
        if (
            self.baseline_genome_ref == self.challenger_genome_ref
            or metric_names != tuple(sorted(set(metric_names)))
            or max(self.baseline_exact_matches, self.challenger_exact_matches) > self.case_count
            or primary is None
            or (
                primary.baseline_score,
                primary.challenger_score,
            )
            != (self.baseline_score, self.challenger_score)
            or (
                primary.case_count,
                primary.baseline_exact_matches,
                primary.challenger_exact_matches,
            )
            != (self.case_count, self.baseline_exact_matches, self.challenger_exact_matches)
            or self.baseline_score
            != Decimal(self.baseline_exact_matches) / Decimal(self.case_count)
            or self.challenger_score
            != Decimal(self.challenger_exact_matches) / Decimal(self.case_count)
            or len(diagnostic_ids) != self.case_count
            or diagnostic_ids != tuple(sorted(set(diagnostic_ids)))
            or sum(row.baseline_correct for row in self.case_diagnostics)
            != self.baseline_exact_matches
            or sum(row.challenger_correct for row in self.case_diagnostics)
            != self.challenger_exact_matches
        ):
            raise ValueError("Primary score and case diagnostics must match measured outcomes")
        if self.schema_version == "1.0":
            if any(row.weakness_categories is not None for row in self.case_diagnostics):
                raise ValueError("Scorecard v1.0 cannot contain weakness categories")
        elif any(
            row.weakness_categories is None
            or (row.challenger_correct and row.weakness_categories)
            or (not row.challenger_correct and not row.weakness_categories)
            for row in self.case_diagnostics
        ):
            raise ValueError("Scorecard weakness categories differ from correctness")
        exact_delta = Fraction(
            self.challenger_exact_matches - self.baseline_exact_matches, self.case_count
        )
        expected = exact_delta >= Fraction(self.minimum_improvement) and exact_delta > 0
        if self.minimum_improvement_met is not expected:
            raise ValueError("Improvement result differs from measured scores")
        all_guardrails = all(row.within_guardrail for row in self.metrics)
        expected_diagnostic = (
            "GUARDRAIL_REGRESSION"
            if not all_guardrails
            else "IMPROVES_ON_TRAINING_DATA"
            if expected
            else "NO_IMPROVEMENT"
        )
        if self.diagnostic != expected_diagnostic:
            raise ValueError("Diagnostic differs from measured improvements and guardrails")
        return self

    @property
    def scorecard_ref(self) -> str:
        return "evolution-scorecard:" + scorecard_sha256(self)


class TournamentRanking(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rank: int = Field(strict=True, ge=1, le=128)
    challenger_genome_ref: str = Field(pattern=r"^agent-genome:[a-f0-9]{64}$")
    scorecard_ref: str = Field(pattern=r"^evolution-scorecard:[a-f0-9]{64}$")
    exact_matches: int = Field(strict=True, ge=0, le=128)
    case_count: int = Field(strict=True, ge=1, le=128)


class EvolutionTournament(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    scope: Literal["TRAINING_TOURNAMENT"] = "TRAINING_TOURNAMENT"
    period: str = Field(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")
    protocol_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    dataset_ref: str = Field(pattern=r"^evolution-dataset:[a-f0-9]{64}$")
    baseline_genome_ref: str = Field(pattern=r"^agent-genome:[a-f0-9]{64}$")
    primary_metric: Literal["CORRECTNESS", "REGIME_CORRECTNESS", "INTEGRITY_CORRECTNESS"]
    rankings: tuple[TournamentRanking, ...] = Field(min_length=1, max_length=128)
    evaluation_verified: Literal[False] = False
    promotion_allowed: Literal[False] = False

    @model_validator(mode="after")
    def ranking_consistency(self):
        if (
            tuple(row.rank for row in self.rankings) != tuple(range(1, len(self.rankings) + 1))
            or len({row.challenger_genome_ref for row in self.rankings}) != len(self.rankings)
            or len({row.scorecard_ref for row in self.rankings}) != len(self.rankings)
            or len({row.case_count for row in self.rankings}) != 1
            or any(row.exact_matches > row.case_count for row in self.rankings)
        ):
            raise ValueError("Tournament rankings must have unique, complete candidate evidence")
        expected = tuple(
            sorted(
                self.rankings,
                key=lambda row: (
                    -Fraction(row.exact_matches, row.case_count),
                    row.challenger_genome_ref,
                ),
            )
        )
        if expected != self.rankings:
            raise ValueError("Tournament rankings differ from exact measured outcomes")
        return self

    @property
    def tournament_ref(self) -> str:
        return "evolution-tournament:" + tournament_sha256(self)


def build_monthly_tournament(
    protocol: EvaluationProtocol,
    scorecards: tuple[EvolutionScorecard, ...],
    *,
    period: str,
) -> EvolutionTournament:
    """Rank complete monthly training diagnostics; this carries no promotion authority."""
    if type(protocol) is not EvaluationProtocol:
        raise ValueError("Retained evaluation protocol required")
    if not isinstance(scorecards, tuple) or not 1 <= len(scorecards) <= 128:
        raise ValueError("Tournament requires a bounded scorecard inventory")
    if type(period) is not str or not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", period):
        raise ValueError("Tournament period must be a canonical YYYY-MM month")
    candidate_refs = []
    baseline_refs = set()
    for scorecard in scorecards:
        if type(scorecard) is not EvolutionScorecard:
            raise ValueError("Tournament scorecards must be retained typed artifacts")
        if scorecard.evaluated_at.strftime("%Y-%m") != period:
            raise ValueError("Tournament scorecard evaluated_at is outside the requested period")
        if (
            scorecard.protocol_sha256 != protocol.protocol_sha256
            or scorecard.dataset_ref != protocol.dataset_ref
            or scorecard.primary_metric != protocol.primary_metric
        ):
            raise ValueError("Tournament scorecards must share one retained protocol and dataset")
        candidate_refs.append(scorecard.challenger_genome_ref)
        baseline_refs.add(scorecard.baseline_genome_ref)
    if len(candidate_refs) != len(set(candidate_refs)):
        raise ValueError("Tournament requires one scorecard per unique challenger")
    if len(baseline_refs) != 1:
        raise ValueError("Tournament scorecards must share one baseline genome")
    ordered = sorted(
        scorecards,
        key=lambda row: (
            -Fraction(row.challenger_exact_matches, row.case_count),
            row.challenger_genome_ref,
        ),
    )
    return EvolutionTournament(
        period=period,
        protocol_sha256=protocol.protocol_sha256,
        dataset_ref=protocol.dataset_ref,
        baseline_genome_ref=ordered[0].baseline_genome_ref,
        primary_metric=protocol.primary_metric,
        rankings=tuple(
            TournamentRanking(
                rank=rank,
                challenger_genome_ref=row.challenger_genome_ref,
                scorecard_ref=row.scorecard_ref,
                exact_matches=row.challenger_exact_matches,
                case_count=row.case_count,
            )
            for rank, row in enumerate(ordered, start=1)
        ),
    )


def encode_scorecard(scorecard: EvolutionScorecard) -> bytes:
    payload = scorecard.model_dump(mode="json")
    if scorecard.schema_version == "1.0":
        for row in payload["case_diagnostics"]:
            row.pop("weakness_categories", None)
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def encode_tournament(tournament: EvolutionTournament) -> bytes:
    return json.dumps(
        tournament.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def scorecard_sha256(scorecard: EvolutionScorecard) -> str:
    return sha256(encode_scorecard(scorecard)).hexdigest()


def tournament_sha256(tournament: EvolutionTournament) -> str:
    return sha256(encode_tournament(tournament)).hexdigest()


def evolution_scorecard_schema() -> dict[str, object]:
    schema = EvolutionScorecard.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "urn:ai-quant-os:evolution-scorecard:1.3"
    schema["allOf"] = [
        {
            "if": {
                "properties": {"schema_version": {"const": "1.0"}},
                "required": ["schema_version"],
            },
            "then": {
                "properties": {
                    "case_diagnostics": {
                        "items": {"properties": {"weakness_categories": {"type": "null"}}}
                    }
                }
            },
        },
        {
            "if": {
                "properties": {"schema_version": {"enum": ["1.1", "1.2", "1.3"]}},
                "required": ["schema_version"],
            },
            "then": {
                "properties": {
                    "case_diagnostics": {
                        "items": {
                            "required": ["weakness_categories"],
                            "properties": {
                                "weakness_categories": {
                                    "type": "array",
                                    "maxItems": 8,
                                    "uniqueItems": True,
                                    "items": {
                                        "enum": [
                                            "REGIME_LABEL_MISMATCH",
                                            "REGIME_EVIDENCE_MISMATCH",
                                            "REGIME_DISTRIBUTION_MISMATCH",
                                            "INTEGRITY_STATUS_MISMATCH",
                                            "INSTRUMENT_FINDINGS_MISMATCH",
                                            "QUARANTINE_SET_MISMATCH",
                                            "INGRESS_COUNTS_MISMATCH",
                                            "CLOCK_EVIDENCE_MISMATCH",
                                            "CONTRADICTION_SET_MISMATCH",
                                            "OUTPUT_SHAPE_MISMATCH",
                                            "OTHER_VALUE_MISMATCH",
                                        ]
                                    },
                                }
                            },
                        }
                    }
                }
            },
        },
    ]
    return schema


def evolution_tournament_schema() -> dict[str, object]:
    schema = EvolutionTournament.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "urn:ai-quant-os:evolution-tournament:1.0"
    return schema


def evolution_output_bundle_schema() -> dict[str, object]:
    schema = EvaluationOutputBundle.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "urn:ai-quant-os:evolution-output-bundle:1.0"
    return schema


def _values_for_key(value: object, key: str) -> tuple[object, ...]:
    found = []
    if isinstance(value, dict):
        if key in value:
            found.append(value[key])
        for child in value.values():
            found.extend(_values_for_key(child, key))
    elif isinstance(value, list):
        for child in value:
            found.extend(_values_for_key(child, key))
    return tuple(found)


def _has_shape_difference(expected: object, observed: object) -> bool:
    if type(expected) is not type(observed):
        return True
    if isinstance(expected, dict):
        common = expected.keys() & observed.keys()
        return set(expected) != set(observed) or any(
            _has_shape_difference(expected[key], observed[key]) for key in common
        )
    if isinstance(expected, list):
        return len(expected) != len(observed) or any(
            _has_shape_difference(left, right) for left, right in zip(expected, observed)
        )
    return False


def _weakness_categories(
    agent_id: str,
    expected_raw: str,
    observed_raw: str,
    *,
    metric: str = "CORRECTNESS",
) -> tuple[str, ...]:
    expected, observed = json.loads(expected_raw), json.loads(observed_raw)
    if metric == "REGIME_CORRECTNESS":
        expected_signature = _regime_signature(expected)
        observed_signature = _regime_signature(observed)
        if expected_signature is None:
            raise ValueError("Expected regime cases require available classified horizons")
        if expected_signature == observed_signature:
            return ()
        if observed_signature is None or tuple(row[:2] for row in expected_signature) != tuple(
            row[:2] for row in observed_signature
        ):
            return ("REGIME_EVIDENCE_MISMATCH",)
        return ("REGIME_LABEL_MISMATCH",)
    if metric == "INTEGRITY_CORRECTNESS":
        expected_signature = _integrity_signature(expected)
        observed_signature = _integrity_signature(observed)
        if expected_signature is None:
            raise ValueError("Expected integrity cases require complete health evidence")
        if observed_signature is None:
            return ("OUTPUT_SHAPE_MISMATCH",)
        if expected_signature == observed_signature:
            return ()
        categories = set()
        for index, category in enumerate(
            (
                "INTEGRITY_STATUS_MISMATCH",
                "INSTRUMENT_FINDINGS_MISMATCH",
                "QUARANTINE_SET_MISMATCH",
                "INGRESS_COUNTS_MISMATCH",
                "CLOCK_EVIDENCE_MISMATCH",
                "CONTRADICTION_SET_MISMATCH",
            )
        ):
            if expected_signature[index] != observed_signature[index]:
                categories.add(category)
        return tuple(sorted(categories or {"OTHER_VALUE_MISMATCH"}))
    if expected == observed:
        return ()
    categories = set()
    if agent_id == "A02":
        if _values_for_key(expected, "regime") != _values_for_key(observed, "regime"):
            categories.add("REGIME_LABEL_MISMATCH")
        if any(
            _values_for_key(expected, key) != _values_for_key(observed, key)
            for key in ("status", "bar_count", "observed_span_seconds")
        ):
            categories.add("REGIME_EVIDENCE_MISMATCH")
        if _values_for_key(expected, "distribution") != _values_for_key(observed, "distribution"):
            categories.add("REGIME_DISTRIBUTION_MISMATCH")
        if expected.get("contradictions") != observed.get("contradictions"):
            categories.add("CONTRADICTION_SET_MISMATCH")
    else:
        for key, category in (
            ("status", "INTEGRITY_STATUS_MISMATCH"),
            ("instruments", "INSTRUMENT_FINDINGS_MISMATCH"),
            ("quarantined_instruments", "QUARANTINE_SET_MISMATCH"),
            ("ingress_counts", "INGRESS_COUNTS_MISMATCH"),
            ("clock_status", "CLOCK_EVIDENCE_MISMATCH"),
            ("contradictions", "CONTRADICTION_SET_MISMATCH"),
        ):
            if expected.get(key) != observed.get(key):
                categories.add(category)
    if _has_shape_difference(expected, observed):
        categories.add("OUTPUT_SHAPE_MISMATCH")
    if not categories:
        categories.add("OTHER_VALUE_MISMATCH")
    return tuple(sorted(categories))


_REGIME_LABELS = {"TRENDING", "RANGING", "HIGH_VOLATILITY", "UNKNOWN"}
_INTEGRITY_STATUSES = {"UNUSABLE", "DEGRADED", "HEALTHY"}
_INTEGRITY_CLOCK_STATUSES = {"UNVERIFIED", "MEASURED"}


def _regime_signature(value: object) -> tuple[tuple[str, str, str], ...] | None:
    if not isinstance(value, dict):
        return None
    if "regime" in value:
        label = value["regime"]
        if type(label) is str and label in _REGIME_LABELS:
            return (("", "", label),)
        return None
    assessments = value.get("assessments")
    if not isinstance(assessments, list) or not assessments:
        return None
    signature = []
    for assessment in assessments:
        if not isinstance(assessment, dict) or not isinstance(assessment.get("instrument_id"), str):
            return None
        horizons = assessment.get("horizons")
        if not isinstance(horizons, list):
            return None
        for horizon in horizons:
            if not isinstance(horizon, dict) or not isinstance(horizon.get("horizon"), str):
                return None
            if horizon.get("status") != "AVAILABLE":
                continue
            label = horizon.get("regime")
            if type(label) is not str or label not in _REGIME_LABELS:
                return None
            signature.append((assessment["instrument_id"], horizon["horizon"], label))
    if not signature or len({row[:2] for row in signature}) != len(signature):
        return None
    return tuple(sorted(signature))


def _integrity_signature(value: object) -> tuple[object, ...] | None:
    if not isinstance(value, dict):
        return None
    status = value.get("status")
    raw_findings = value.get("instruments")
    quarantine = value.get("quarantined_instruments")
    counts = value.get("ingress_counts")
    clock = value.get("clock_status")
    contradictions = value.get("contradictions")
    if (
        type(status) is not str
        or status not in _INTEGRITY_STATUSES
        or not isinstance(raw_findings, list)
        or not isinstance(quarantine, list)
        or not isinstance(counts, dict)
        or set(counts) != CODES
        or any(
            type(key) is not str or type(count) is not int or not 0 <= count <= 2**31 - 1
            for key, count in counts.items()
        )
        or type(clock) is not str
        or clock not in _INTEGRITY_CLOCK_STATUSES
        or not isinstance(contradictions, list)
        or any(type(item) is not str for item in (*quarantine, *contradictions))
        or len(set(quarantine)) != len(quarantine)
        or len(set(contradictions)) != len(contradictions)
    ):
        return None
    findings = []
    quarantined_by_findings = []
    for finding in raw_findings:
        if (
            not isinstance(finding, dict)
            or type(finding.get("instrument_id")) is not str
            or not finding["instrument_id"]
            or type(finding.get("usable")) is not bool
            or not isinstance(finding.get("issues"), list)
            or not isinstance(finding.get("affected_features"), list)
            or any(
                type(item) is not str
                for item in (*finding["issues"], *finding["affected_features"])
            )
            or len(set(finding["issues"])) != len(finding["issues"])
            or len(set(finding["affected_features"])) != len(finding["affected_features"])
            or finding["usable"] != (not finding["issues"])
        ):
            return None
        if not finding["usable"]:
            quarantined_by_findings.append(finding["instrument_id"])
        findings.append(
            (
                finding["instrument_id"],
                finding["usable"],
                tuple(sorted(finding["issues"])),
                tuple(sorted(finding["affected_features"])),
            )
        )
    if (
        not findings
        or len({finding[0] for finding in findings}) != len(findings)
        or tuple(sorted(quarantine)) != tuple(sorted(quarantined_by_findings))
    ):
        return None
    return (
        status,
        tuple(sorted(findings)),
        tuple(sorted(quarantine)),
        tuple(sorted(counts.items())),
        clock,
        tuple(sorted(contradictions)),
    )


def _exact_matches(outputs: tuple[CaseOutput, ...], dataset: EvolutionDataset) -> int:
    expected_ids = tuple(case.case_id for case in dataset.cases)
    actual_ids = tuple(row.case_id for row in outputs)
    if actual_ids != expected_ids:
        raise ValueError("Evaluation requires the exact dataset case inventory in order")
    return sum(
        output.output_json == case.expected_json
        for case, output in zip(dataset.cases, outputs, strict=True)
    )


def _metric_matches(
    metric: str,
    agent_id: str,
    outputs: tuple[CaseOutput, ...],
    dataset: EvolutionDataset,
) -> int:
    if metric == "CORRECTNESS":
        return _exact_matches(outputs, dataset)
    if metric not in {"REGIME_CORRECTNESS", "INTEGRITY_CORRECTNESS"}:
        raise ValueError("No deterministic adapter is available for the requested metric")
    _exact_matches_inventory(outputs, dataset)
    expected_agent = "A02" if metric == "REGIME_CORRECTNESS" else "A07"
    if agent_id != expected_agent:
        raise ValueError(f"{metric} adapter is currently specific to {expected_agent}")
    matches = 0
    for case, output in zip(dataset.cases, outputs, strict=True):
        signature = _regime_signature if metric == "REGIME_CORRECTNESS" else _integrity_signature
        expected = signature(json.loads(case.expected_json))
        if expected is None:
            required = (
                "available classified horizons"
                if metric == "REGIME_CORRECTNESS"
                else "complete health evidence"
            )
            raise ValueError(f"Expected {metric} cases require {required}")
        observed = signature(json.loads(output.output_json))
        matches += expected == observed
    return matches


def _exact_matches_inventory(outputs: tuple[CaseOutput, ...], dataset: EvolutionDataset) -> None:
    expected_ids = tuple(case.case_id for case in dataset.cases)
    actual_ids = tuple(row.case_id for row in outputs)
    if actual_ids != expected_ids:
        raise ValueError("Evaluation requires the exact dataset case inventory in order")


def _metric_case_matches(metric: str, agent_id: str, expected: str, observed: str) -> bool:
    if metric == "CORRECTNESS":
        return observed == expected
    if metric == "REGIME_CORRECTNESS" and agent_id == "A02":
        expected_signature = _regime_signature(json.loads(expected))
        if expected_signature is None:
            raise ValueError("Expected regime cases require available classified horizons")
        return expected_signature == _regime_signature(json.loads(observed))
    if metric == "INTEGRITY_CORRECTNESS" and agent_id == "A07":
        expected_signature = _integrity_signature(json.loads(expected))
        if expected_signature is None:
            raise ValueError("Expected integrity cases require complete health evidence")
        return expected_signature == _integrity_signature(json.loads(observed))
    raise ValueError("No deterministic adapter is available for the requested metric")


def evaluate_protocol_outputs(
    protocol: EvaluationProtocol,
    dataset: EvolutionDataset,
    baseline: AgentGenome,
    challenger: AgentGenome,
    baseline_outputs: tuple[CaseOutput, ...],
    challenger_outputs: tuple[CaseOutput, ...],
    *,
    evaluated_at: datetime,
) -> EvolutionScorecard:
    """Compare supported deterministic metrics on retained training data.

    ``CORRECTNESS`` requires exact canonical output equality. A02
    ``REGIME_CORRECTNESS`` compares classified regime labels by instrument and
    horizon, while A07 ``INTEGRITY_CORRECTNESS`` compares health status and
    evidence fields. Both semantic adapters ignore unrelated envelope metadata.
    Output sets are supplied by the caller and are not signed execution receipts.

    The scorecard therefore remains explicitly unverified and has no promotion
    authority, regardless of its measured training-set result.
    """
    if type(protocol) is not EvaluationProtocol or type(dataset) is not EvolutionDataset:
        raise ValueError("Retained typed protocol and dataset are required")
    if type(baseline) is not AgentGenome or type(challenger) is not AgentGenome:
        raise ValueError("Typed baseline and challenger genomes are required")
    if dataset.resource_class != "TRAINING":
        raise ValueError("Training diagnostics accept training datasets only")
    if protocol.dataset_ref != dataset.dataset_ref:
        raise ValueError("Protocol and dataset identities differ")
    require_protocol_genome(protocol, baseline)
    require_protocol_genome(protocol, challenger)
    if challenger.parent_ref != baseline.genome_ref:
        raise ValueError("Evaluation requires a direct challenger of the baseline")
    require_genome_child(baseline, challenger)
    methods = {
        "EXACT_OBJECT_MATCH",
        "REGIME_EXACT_OBJECT_MATCH",
        "INTEGRITY_EVIDENCE_MATCH",
    }
    if any(row.method not in methods for row in protocol.criteria):
        raise ValueError("Protocol contains a metric without a deterministic output adapter")

    case_count = len(dataset.cases)
    if (
        any(row.criterion.metric == "REGIME_CORRECTNESS" for row in protocol.criteria)
        and protocol.agent_id != "A02"
    ):
        raise ValueError("Regime correctness adapter is currently specific to A02")
    if (
        any(row.criterion.metric == "INTEGRITY_CORRECTNESS" for row in protocol.criteria)
        and protocol.agent_id != "A07"
    ):
        raise ValueError("Integrity correctness adapter is currently specific to A07")
    comparisons_list = []
    for criterion in protocol.criteria:
        metric = criterion.criterion.metric
        baseline_matches = _metric_matches(metric, protocol.agent_id, baseline_outputs, dataset)
        challenger_matches = _metric_matches(metric, protocol.agent_id, challenger_outputs, dataset)
        delta = Fraction(challenger_matches - baseline_matches, case_count)
        comparisons_list.append(
            MetricComparison(
                metric=metric,
                baseline_score=Decimal(baseline_matches) / Decimal(case_count),
                challenger_score=Decimal(challenger_matches) / Decimal(case_count),
                case_count=case_count,
                baseline_exact_matches=baseline_matches,
                challenger_exact_matches=challenger_matches,
                maximum_regression=criterion.maximum_regression,
                within_guardrail=delta >= -Fraction(criterion.maximum_regression),
            )
        )
    comparisons = tuple(comparisons_list)
    primary = next(
        row for row in protocol.criteria if row.criterion.metric == protocol.primary_metric
    )
    primary_comparison = next(row for row in comparisons if row.metric == protocol.primary_metric)
    baseline_exact_matches = primary_comparison.baseline_exact_matches
    challenger_exact_matches = primary_comparison.challenger_exact_matches
    baseline_score = primary_comparison.baseline_score
    challenger_score = primary_comparison.challenger_score
    exact_delta = Fraction(challenger_exact_matches - baseline_exact_matches, case_count)
    case_diagnostics = tuple(
        CaseDiagnostic(
            case_id=case.case_id,
            baseline_correct=_metric_case_matches(
                protocol.primary_metric,
                protocol.agent_id,
                case.expected_json,
                baseline_output.output_json,
            ),
            challenger_correct=_metric_case_matches(
                protocol.primary_metric,
                protocol.agent_id,
                case.expected_json,
                challenger_output.output_json,
            ),
            weakness_categories=_weakness_categories(
                protocol.agent_id,
                case.expected_json,
                challenger_output.output_json,
                metric=protocol.primary_metric,
            ),
        )
        for case, baseline_output, challenger_output in zip(
            dataset.cases, baseline_outputs, challenger_outputs, strict=True
        )
    )
    improvement = primary.criterion.minimum_improvement
    improvement_met = exact_delta >= Fraction(improvement) and exact_delta > 0
    diagnostic = (
        "GUARDRAIL_REGRESSION"
        if not all(row.within_guardrail for row in comparisons)
        else "IMPROVES_ON_TRAINING_DATA"
        if improvement_met
        else "NO_IMPROVEMENT"
    )
    return EvolutionScorecard(
        diagnostic=diagnostic,
        dataset_ref=dataset.dataset_ref,
        dataset_provenance=dataset.provenance,
        protocol_sha256=protocol.protocol_sha256,
        baseline_genome_ref=baseline.genome_ref,
        challenger_genome_ref=challenger.genome_ref,
        primary_metric=protocol.primary_metric,
        baseline_score=baseline_score,
        challenger_score=challenger_score,
        case_count=case_count,
        baseline_exact_matches=baseline_exact_matches,
        challenger_exact_matches=challenger_exact_matches,
        minimum_improvement=improvement,
        minimum_improvement_met=improvement_met,
        metrics=comparisons,
        case_diagnostics=case_diagnostics,
        evaluated_at=evaluated_at,
    )
