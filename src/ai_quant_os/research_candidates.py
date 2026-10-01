"""Portable research dossiers derived exclusively from completed, verified experiments."""

from dataclasses import dataclass
from hashlib import sha256
import re
from typing import Literal

from ai_quant_os.research_results import ResearchReport
from ai_quant_os.research_store import encode_research
from ai_quant_os.strategy_contracts import StrategyGenome


@dataclass(frozen=True)
class CandidateManifest:
    schema_version: Literal["1.0"]
    candidate_id: str
    experiment_id: str
    hypothesis_id: str
    report_sha256: str
    plan_sha256: str
    selected_genome: StrategyGenome | None
    research_outcome: Literal["FAILED", "REJECTED", "VALIDATION_INCOMPLETE"]
    blocking_gates: tuple[str, ...]
    reasons: tuple[str, ...]
    promotion_allowed: Literal[False]

    def __post_init__(self):
        if self.schema_version != "1.0" or self.promotion_allowed is not False:
            raise ValueError("Candidate dossiers cannot grant promotion authority")
        if any(
            not re.fullmatch(r"[a-f0-9]{64}", value)
            for value in (self.candidate_id, self.report_sha256, self.plan_sha256)
        ):
            raise ValueError("Candidate fingerprint required")


def candidate_manifest(report: ResearchReport) -> CandidateManifest:
    digest = sha256(encode_research(report)).hexdigest()
    return CandidateManifest(
        "1.0",
        sha256(f"candidate-v1:{report.plan.experiment_id}:{digest}".encode()).hexdigest(),
        report.plan.experiment_id,
        report.plan.hypothesis.hypothesis_id,
        digest,
        sha256(encode_research(report.plan)).hexdigest(),
        report.selection.genome if report.selection is not None else None,
        report.outcome,
        tuple(sorted(g.name for g in report.gates if g.state != "PASS")),
        report.reasons,
        False,
    )


@dataclass(frozen=True)
class CandidateSource:
    sha256: str
    text: str

    def __post_init__(self):
        if (
            not isinstance(self.text, str)
            or len(self.text.encode()) > 1048576
            or sha256(self.text.encode()).hexdigest() != self.sha256
        ):
            raise ValueError("Candidate source fingerprint mismatch")


@dataclass(frozen=True)
class CandidateBundle:
    schema_version: Literal["1.0"]
    manifest: CandidateManifest
    report: ResearchReport
    sources: tuple[CandidateSource, ...]

    def __post_init__(self):
        if self.schema_version != "1.0" or self.manifest != candidate_manifest(self.report):
            raise ValueError("Candidate manifest differs from archived research evidence")
        evidence = []
        if self.report.selection is not None:
            evidence.extend(self.report.selection.training_results)
        for fold in self.report.folds:
            evidence.extend((*fold.training_results, fold.validation, fold.stress))
        evidence.extend(
            run for run in (self.report.holdout, self.report.holdout_stress) if run is not None
        )
        if self.report.adversarial is not None:
            evidence.extend(row.run for row in self.report.adversarial.scenarios)
        if any(run not in self.report.attempts for run in evidence):
            raise ValueError("Candidate native evidence was omitted from completed attempts")
        expected = {
            source.sha256
            for run in self.report.attempts
            for source in run.binding.implementation.source_files
        }
        if (
            type(self.sources) is not tuple
            or len(self.sources) > 256
            or tuple(row.sha256 for row in self.sources) != tuple(sorted(expected))
        ):
            raise ValueError("Candidate source inventory differs from native evidence")


def build_candidate_bundle(research, strategies, experiment_id: str) -> CandidateBundle:
    report = research.get(experiment_id)
    if report is None:
        raise ValueError("A completed verified research experiment is required")
    sources = {}
    implementations = {
        run.binding.implementation.fingerprint: run.binding.implementation
        for run in report.attempts
    }
    for implementation in implementations.values():
        if strategies.get(implementation.strategy_ref) != implementation:
            raise ValueError("Candidate source registry differs from archived implementation")
        archived = strategies.archived_sources(implementation.strategy_ref)
        for source in implementation.source_files:
            sources[source.sha256] = CandidateSource(
                source.sha256, archived[source.path].decode("utf-8")
            )
    return CandidateBundle(
        "1.0", candidate_manifest(report), report, tuple(sources[key] for key in sorted(sources))
    )
