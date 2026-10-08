"""Bounded metadata previews of verified dossiers; source text is never exposed."""

from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field


class GatePreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(max_length=64)
    state: Literal["PASS", "FAIL", "PENDING"]
    reason: str = Field(max_length=256)


class DossierPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    candidate_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    experiment_id: str = Field(max_length=64)
    hypothesis_id: str = Field(max_length=64)
    hypothesis: str = Field(max_length=1024)
    state: Literal["CHALLENGER", "FAILED", "REJECTED", "WITHDRAWN"]
    outcome: Literal["FAILED", "REJECTED", "VALIDATION_INCOMPLETE"]
    revision: int = Field(strict=True, ge=1)
    report_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    last_event_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    strategy_ref: str | None = Field(max_length=128)
    fold_count: int = Field(strict=True, ge=0)
    attempt_count: int = Field(strict=True, ge=0, le=128)
    gates: tuple[GatePreview, ...] = Field(max_length=16)
    reasons: tuple[Annotated[str, Field(max_length=256)], ...] = Field(max_length=16)
    details_truncated: bool
    promotion_allowed: Literal[False] = False


class ResearchDossierPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    mode: Literal["PAPER"] = "PAPER"
    head_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    total: int = Field(strict=True, ge=0, le=1000)
    offset: int = Field(strict=True, ge=0, le=1000)
    limit: int = Field(strict=True, ge=1, le=2)
    next_offset: int | None
    candidates: tuple[DossierPreview, ...] = Field(max_length=2)


def dossier_preview(view, bundle) -> DossierPreview:
    manifest, report = view.manifest, bundle.report
    truncated = (
        len(report.plan.hypothesis.description) > 1024
        or len(report.gates) > 16
        or len(report.reasons) > 16
        or any(len(g.name) > 64 or len(g.reason) > 256 for g in report.gates)
        or any(len(reason) > 256 for reason in report.reasons)
    )
    return DossierPreview(
        candidate_id=manifest.candidate_id,
        experiment_id=manifest.experiment_id,
        hypothesis_id=manifest.hypothesis_id,
        hypothesis=report.plan.hypothesis.description[:1024],
        state=view.state,
        outcome=manifest.research_outcome,
        revision=view.revision,
        report_sha256=manifest.report_sha256,
        plan_sha256=manifest.plan_sha256,
        last_event_sha256=view.last_event_sha256,
        strategy_ref=manifest.selected_genome.strategy_ref if manifest.selected_genome else None,
        fold_count=len(report.folds),
        attempt_count=len(report.attempts),
        gates=tuple(
            GatePreview(name=g.name[:64], state=g.state, reason=g.reason[:256])
            for g in report.gates[:16]
        ),
        reasons=tuple(reason[:256] for reason in report.reasons[:16]),
        details_truncated=truncated,
    )
