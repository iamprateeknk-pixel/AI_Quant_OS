"""Signed promotion policy for agent evolution; the gate automatic promotion obeys.

Automatic promotion of an evolved agent genome is disabled by default and
stays disabled until a *signed promotion policy* exists. A policy is a
canonical-JSON payload signed with Ed25519 by an authorized quorum of
promotion signers; signer keys are provisioned through a trust store that is
itself signed by a separately held root key (the same envelope discipline as
:mod:`ai_quant_os.repair_certificates`). Verification is fail-closed:
unknown signer, invalid or missing signature, insufficient quorum, expired
policy, or an agent outside the policy scope all refuse.

Even a fully authorized decision never applies a promotion inside this
module: ``promotion_applied`` is always False. Applying an authorized genome
as the served agent version remains a release action (a new catalog
version), performed outside the evolution subsystem by its owner.
"""

import base64
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from types import MappingProxyType
from collections.abc import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_quant_os.evolution_holdout import HoldoutEvaluation
from ai_quant_os.repair_snapshots import canonical_json

_SIGNER_ID = re.compile(r"[a-z][a-z0-9-]{0,63}")
_AGENT_ID = re.compile(r"A(0[1-9]|1[0-8])")
_SIGNATURE_TEXT = re.compile(r"[A-Za-z0-9_-]{86}")
_PUBLIC_KEY_TEXT = re.compile(r"[A-Za-z0-9_-]{43}")


def _parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _decode_signature(text: object) -> bytes:
    if type(text) is not str or not _SIGNATURE_TEXT.fullmatch(text):
        raise ValueError("Promotion signature encoding is invalid")
    raw = base64.urlsafe_b64decode(text + "==")
    if base64.urlsafe_b64encode(raw).decode().rstrip("=") != text:
        raise ValueError("Promotion signature encoding is invalid")
    return raw


def _decode_public_key(text: object) -> bytes:
    if type(text) is not str or not _PUBLIC_KEY_TEXT.fullmatch(text):
        raise ValueError("Promotion signer public key encoding is invalid")
    raw = base64.urlsafe_b64decode(text + "=")
    if len(raw) != 32 or base64.urlsafe_b64encode(raw).decode().rstrip("=") != text:
        raise ValueError("Promotion signer public key encoding is invalid")
    return raw


@dataclass(frozen=True)
class PromotionTrustStore:
    """Root-signed inventory of keys allowed to sign promotion policies."""

    trusted_public_keys: Mapping[str, bytes]


def load_promotion_trust_store(raw: bytes, *, root_public_key: bytes) -> PromotionTrustStore:
    """Load promotion signer keys under a separately provisioned root key."""
    if (
        type(raw) is not bytes
        or not 1 <= len(raw) <= 16384
        or type(root_public_key) is not bytes
        or len(root_public_key) != 32
    ):
        raise ValueError("Promotion trust inputs are invalid")
    try:
        envelope = json.loads(raw)
        if (
            type(envelope) is not dict
            or set(envelope) != {"store", "signature"}
            or type(envelope["store"]) is not dict
            or set(envelope["store"]) != {"schema_version", "signers"}
            or envelope["store"]["schema_version"] != "1.0"
            or canonical_json(envelope).encode() != raw
        ):
            raise ValueError("Promotion trust store is not canonical or complete")
        signature = _decode_signature(envelope["signature"])
        try:
            Ed25519PublicKey.from_public_bytes(root_public_key).verify(
                signature, canonical_json(envelope["store"]).encode()
            )
        except (InvalidSignature, ValueError) as error:
            raise ValueError("Promotion trust root signature is invalid") from error
        signers = envelope["store"]["signers"]
        if type(signers) is not list or not 1 <= len(signers) <= 32:
            raise ValueError("Promotion trust signer inventory is invalid")
        keys, identities = {}, []
        for signer in signers:
            if (
                type(signer) is not dict
                or set(signer) != {"signer_id", "public_key"}
                or type(signer["signer_id"]) is not str
                or not _SIGNER_ID.fullmatch(signer["signer_id"])
            ):
                raise ValueError("Promotion trust entry is invalid")
            keys[signer["signer_id"]] = _decode_public_key(signer["public_key"])
            identities.append(signer["signer_id"])
        if identities != sorted(set(identities)):
            raise ValueError("Promotion trust entries must be uniquely sorted")
        return PromotionTrustStore(trusted_public_keys=MappingProxyType(keys))
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, KeyError, OverflowError) as error:
        raise ValueError("Promotion trust store is invalid") from error


class PromotionPolicy(BaseModel):
    """The signed payload: who may promote what, under which evidence floor."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "1.0"
    policy_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    policy_version: str = Field(pattern=r"^(?:0|[1-9][0-9]{0,5})(?:\.(?:0|[1-9][0-9]{0,5})){2}$")
    promotion_mode: str = "AUTOMATIC"
    scope_agent_ids: tuple[str, ...] = Field(min_length=1, max_length=18)
    authorized_signer_ids: tuple[str, ...] = Field(min_length=1, max_length=32)
    required_signatures: int = Field(strict=True, ge=1, le=32)
    minimum_holdout_cases: int = Field(strict=True, ge=1, le=128)
    issued_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    expires_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    @field_validator("schema_version")
    @classmethod
    def exact_schema(cls, value: str) -> str:
        if value != "1.0":
            raise ValueError("Promotion policy schema version is unsupported")
        return value

    @field_validator("promotion_mode")
    @classmethod
    def exact_mode(cls, value: str) -> str:
        if value != "AUTOMATIC":
            raise ValueError("Promotion policy mode is unsupported")
        return value

    @model_validator(mode="after")
    def policy_inventory(self) -> "PromotionPolicy":
        if tuple(sorted(set(self.scope_agent_ids))) != self.scope_agent_ids or any(
            not _AGENT_ID.fullmatch(agent) for agent in self.scope_agent_ids
        ):
            raise ValueError("Promotion policy scope must be unique sorted agent ids")
        if tuple(sorted(set(self.authorized_signer_ids))) != self.authorized_signer_ids or any(
            not _SIGNER_ID.fullmatch(signer) for signer in self.authorized_signer_ids
        ):
            raise ValueError("Promotion policy signers must be unique sorted signer ids")
        if self.required_signatures > len(self.authorized_signer_ids):
            raise ValueError("Promotion policy quorum exceeds its authorized signers")
        if not _parse_timestamp(self.issued_at) < _parse_timestamp(self.expires_at):
            raise ValueError("Promotion policy validity window is empty")
        return self


@dataclass(frozen=True)
class PromotionPolicyVerification:
    policy_sha256: str
    policy_id: str
    policy_version: str
    signer_ids: tuple[str, ...]
    scope_agent_ids: tuple[str, ...]
    minimum_holdout_cases: int
    state: str = "POLICY_SIGNATURES_VERIFIED"


def load_signed_promotion_policy(
    raw: bytes, *, trust_store: PromotionTrustStore
) -> tuple[PromotionPolicy, PromotionPolicyVerification]:
    """Parse and signature-verify a promotion policy envelope, fail-closed.

    The envelope is ``{"policy": {...}, "signatures": [{"signer_id": ...,
    "signature": ...}, ...]}`` in canonical JSON. Every signature must come
    from a distinct signer that the policy itself authorizes *and* the trust
    store provisions, and the distinct valid signers must meet the policy's
    own ``required_signatures`` quorum.
    """
    if type(raw) is not bytes or not 1 <= len(raw) <= 65536:
        raise ValueError("Promotion policy envelope is invalid")
    if type(trust_store) is not PromotionTrustStore:
        raise ValueError("Promotion policy requires a verified trust store")
    try:
        envelope = json.loads(raw)
        if (
            type(envelope) is not dict
            or set(envelope) != {"policy", "signatures"}
            or canonical_json(envelope).encode() != raw
            or type(envelope["signatures"]) is not list
            or not 1 <= len(envelope["signatures"]) <= 32
        ):
            raise ValueError("Promotion policy envelope is not canonical or complete")
        policy = PromotionPolicy.model_validate(envelope["policy"])
        payload = canonical_json(envelope["policy"]).encode()
        verified: list[str] = []
        for entry in envelope["signatures"]:
            if (
                type(entry) is not dict
                or set(entry) != {"signer_id", "signature"}
                or type(entry["signer_id"]) is not str
                or not _SIGNER_ID.fullmatch(entry["signer_id"])
            ):
                raise ValueError("Promotion policy signature entry is invalid")
            signer_id = entry["signer_id"]
            if signer_id in verified:
                raise ValueError("Promotion policy signer signed more than once")
            if signer_id not in policy.authorized_signer_ids:
                raise ValueError("Promotion policy signer is not authorized by the policy")
            public_bytes = trust_store.trusted_public_keys.get(signer_id)
            if public_bytes is None:
                raise ValueError("Promotion policy signer is not in the trust store")
            signature = _decode_signature(entry["signature"])
            try:
                Ed25519PublicKey.from_public_bytes(public_bytes).verify(signature, payload)
            except (InvalidSignature, ValueError) as error:
                raise ValueError("Promotion policy signature is invalid") from error
            verified.append(signer_id)
        if len(verified) < policy.required_signatures:
            raise ValueError("Promotion policy signature quorum is not met")
        verification = PromotionPolicyVerification(
            policy_sha256=sha256(raw).hexdigest(),
            policy_id=policy.policy_id,
            policy_version=policy.policy_version,
            signer_ids=tuple(sorted(verified)),
            scope_agent_ids=policy.scope_agent_ids,
            minimum_holdout_cases=policy.minimum_holdout_cases,
        )
        return policy, verification
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, KeyError, OverflowError) as error:
        raise ValueError("Promotion policy is invalid") from error


class PromotionGateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "1.0"
    decision: str = Field(pattern=r"^(AUTHORIZED|REFUSED)$")
    reasons: tuple[str, ...] = Field(max_length=8)
    policy_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    holdout_evaluation_ref: str | None = None
    challenger_genome_ref: str = Field(pattern=r"^agent-genome:[a-f0-9]{64}$")
    agent_id: str = Field(pattern=r"^A(0[1-9]|1[0-8])$")
    promotion_applied: bool = False
    decided_at: datetime

    @field_validator("promotion_applied", mode="before")
    @classmethod
    def never_applied_here(cls, value: object) -> bool:
        if value is not False:
            raise ValueError("The promotion gate authorizes; it never applies a promotion")
        return False


@dataclass(frozen=True)
class EvolutionPromotionGate:
    """Decides whether automatic promotion of one challenger is authorized.

    Authorization requires all of: a signature-verified, in-window signed
    policy whose scope covers the agent; an independent holdout evaluation
    with verdict HOLDOUT_PASS for exactly this challenger; and at least the
    policy's minimum holdout case count. Anything missing refuses.
    """

    trust_store: PromotionTrustStore

    def decide(
        self,
        *,
        policy_raw: bytes | None,
        holdout: HoldoutEvaluation | None,
        challenger_genome_ref: str,
        agent_id: str,
        now: datetime,
    ) -> PromotionGateDecision:
        def refused(
            reasons: tuple[str, ...], policy_sha256: str | None = None
        ) -> PromotionGateDecision:
            return PromotionGateDecision(
                decision="REFUSED",
                reasons=reasons,
                policy_sha256=policy_sha256,
                holdout_evaluation_ref=holdout.evaluation_ref if holdout is not None else None,
                challenger_genome_ref=challenger_genome_ref,
                agent_id=agent_id,
                decided_at=now,
            )

        if policy_raw is None:
            return refused(("SIGNED_POLICY_MISSING",))
        try:
            policy, verification = load_signed_promotion_policy(
                policy_raw, trust_store=self.trust_store
            )
        except ValueError:
            return refused(("SIGNED_POLICY_INVALID",))
        if not _parse_timestamp(policy.issued_at) <= now < _parse_timestamp(policy.expires_at):
            return refused(("SIGNED_POLICY_OUTSIDE_VALIDITY_WINDOW",), verification.policy_sha256)
        if agent_id not in policy.scope_agent_ids:
            return refused(("SIGNED_POLICY_SCOPE_EXCLUDES_AGENT",), verification.policy_sha256)
        reasons: list[str] = []
        if holdout is None:
            reasons.append("HOLDOUT_EVALUATION_MISSING")
        else:
            if holdout.verdict != "HOLDOUT_PASS":
                reasons.append("HOLDOUT_VERDICT_NOT_PASS")
            if holdout.challenger_genome_ref != challenger_genome_ref:
                reasons.append("HOLDOUT_CHALLENGER_MISMATCH")
            if holdout.agent_id != agent_id:
                reasons.append("HOLDOUT_AGENT_MISMATCH")
            if holdout.case_count < policy.minimum_holdout_cases:
                reasons.append("HOLDOUT_CASES_BELOW_POLICY_MINIMUM")
        if reasons:
            return refused(tuple(reasons), verification.policy_sha256)
        return PromotionGateDecision(
            decision="AUTHORIZED",
            reasons=(),
            policy_sha256=verification.policy_sha256,
            holdout_evaluation_ref=holdout.evaluation_ref if holdout is not None else None,
            challenger_genome_ref=challenger_genome_ref,
            agent_id=agent_id,
            decided_at=now,
        )
