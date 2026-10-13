"""Frozen candidate-to-paper admission bindings; these never grant live authority."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import re
from typing import Literal

from ai_quant_os.contracts import TradeIntent, _require_aware
from ai_quant_os.research_candidate_store import CandidateView
from ai_quant_os.research_candidates import CandidateBundle
from ai_quant_os.research_contracts import _identifier
from ai_quant_os.research_store import encode_research


@dataclass(frozen=True)
class CandidatePaperAssignment:
    assignment_id: str
    candidate_id: str
    candidate_revision: int
    candidate_event_sha256: str
    candidate_bundle_sha256: str
    genome_sha256: str
    strategy_ref: str
    instrument_id: str
    max_exposure: Decimal
    assigned_at: datetime
    expires_at: datetime
    schema_version: Literal["1.0"] = "1.0"
    broker_route: Literal["PAPER"] = "PAPER"
    promotion_allowed: Literal[False] = False

    def __post_init__(self):
        _identifier(self.assignment_id)
        _identifier(self.candidate_id)
        for digest in (
            self.candidate_event_sha256,
            self.candidate_bundle_sha256,
            self.genome_sha256,
        ):
            if not isinstance(digest, str) or re.fullmatch(r"[a-f0-9]{64}", digest) is None:
                raise ValueError("Candidate paper assignment fingerprints must be SHA-256")
        if (
            type(self.candidate_revision) is not int
            or self.candidate_revision != 1
            or not isinstance(self.strategy_ref, str)
            or re.fullmatch(r"[a-z][a-z0-9-]{0,63}@\d+\.\d+\.\d+", self.strategy_ref) is None
            or not isinstance(self.instrument_id, str)
            or not self.instrument_id
            or type(self.max_exposure) is not Decimal
            or not self.max_exposure.is_finite()
            or not Decimal(0) < self.max_exposure <= Decimal("1000")
            or self.schema_version != "1.0"
            or self.broker_route != "PAPER"
            or self.promotion_allowed is not False
        ):
            raise ValueError("Invalid candidate paper assignment")
        _require_aware(self.assigned_at)
        _require_aware(self.expires_at)
        object.__setattr__(self, "assigned_at", self.assigned_at.astimezone(timezone.utc))
        object.__setattr__(self, "expires_at", self.expires_at.astimezone(timezone.utc))
        if not self.assigned_at < self.expires_at <= self.assigned_at + timedelta(days=30):
            raise ValueError("Candidate paper assignment must expire within 30 days")

    def authorize(
        self,
        intent: TradeIntent,
        assignment_id: str,
        view: CandidateView,
        bundle: CandidateBundle,
        *,
        now,
    ):
        _require_aware(now)
        if (
            type(intent) is not TradeIntent
            or type(view) is not CandidateView
            or type(bundle) is not CandidateBundle
        ):
            raise ValueError("Typed paper intent and current candidate snapshot required")
        if (
            view.state != "CHALLENGER"
            or view.revision != self.candidate_revision
            or view.last_event_sha256 != self.candidate_event_sha256
            or view.manifest.candidate_id != self.candidate_id
            or bundle.manifest.candidate_id != self.candidate_id
        ):
            raise ValueError("Candidate paper assignment lifecycle changed")
        if bundle.report.holdout is None:
            raise ValueError("Candidate paper assignment holdout evidence is missing")
        self.authorize_intent(intent, assignment_id, now=now)
        genome = bundle.report.holdout.binding.genome
        if (
            sha256(encode_research(bundle)).hexdigest() != self.candidate_bundle_sha256
            or bundle.report.selection is None
            or bundle.report.selection.genome != genome
            or genome.fingerprint != self.genome_sha256
            or genome.strategy_ref != self.strategy_ref
        ):
            raise ValueError(
                "Candidate paper assignment is stale or does not authorize this intent"
            )

    def authorize_intent(self, intent: TradeIntent, assignment_id: str, *, now):
        self.verify_intent_binding(intent, assignment_id)
        _require_aware(now)
        current = now.astimezone(timezone.utc)
        if (
            current < self.assigned_at
            or current >= self.expires_at
            or intent.created_at > current
            or current >= intent.valid_until
        ):
            raise ValueError("Candidate paper assignment does not authorize this intent")

    def verify_intent_binding(self, intent: TradeIntent, assignment_id: str):
        if type(intent) is not TradeIntent:
            raise ValueError("Typed paper intent required")
        if (
            assignment_id != self.assignment_id
            or f"{intent.strategy_id}@{intent.strategy_version}" != self.strategy_ref
            or intent.instrument_id != self.instrument_id
            or intent.desired_exposure > self.max_exposure
            or intent.created_at < self.assigned_at
            or intent.valid_until > self.expires_at
        ):
            raise ValueError("Candidate paper assignment does not authorize this intent")


def create_candidate_paper_assignment(
    assignment_id,
    view,
    bundle,
    *,
    strategy_id,
    strategy_version,
    instrument_id,
    max_exposure,
    assigned_at,
    expires_at,
):
    if (
        type(view) is not CandidateView
        or type(bundle) is not CandidateBundle
        or view.state != "CHALLENGER"
        or view.revision != 1
        or view.manifest != bundle.manifest
    ):
        raise ValueError("An unchanged challenger candidate is required")
    genome = bundle.report.holdout.binding.genome
    if (
        bundle.report.selection is None
        or bundle.report.selection.genome != genome
        or bundle.manifest.selected_genome != genome
        or instrument_id != bundle.report.plan.holdout_scope.instrument_id
        or f"{strategy_id}@{strategy_version}" != genome.strategy_ref
    ):
        raise ValueError("Paper assignment must match the candidate genome and instrument")
    return CandidatePaperAssignment(
        assignment_id,
        bundle.manifest.candidate_id,
        view.revision,
        view.last_event_sha256,
        sha256(encode_research(bundle)).hexdigest(),
        genome.fingerprint,
        genome.strategy_ref,
        instrument_id,
        max_exposure,
        assigned_at,
        expires_at,
    )
