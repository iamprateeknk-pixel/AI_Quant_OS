"""Verify independently signed behavioral results against an exact repair source snapshot."""

import base64
import json
import re
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from types import MappingProxyType
from collections.abc import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from ai_quant_os.repair_snapshots import RepairSourceSnapshot, canonical_json


_CERTIFICATE_FIELDS = {
    "schema_version",
    "verifier_id",
    "verifier_source_sha256",
    "suite_sha256",
    "image_id",
    "candidate_id",
    "candidate_commit",
    "rollback_commit",
    "snapshot_sha256",
    "request_sha256",
    "incident_id",
    "agent_id",
    "old_version",
    "new_version",
    "replay_sha256",
    "issued_at",
    "verdict",
    "test_results",
    "promotion_allowed",
}
_TEST_ID = re.compile(r"[a-z][a-z0-9-]{0,63}")
_DIGEST = re.compile(r"[a-f0-9]{64}")
_GIT_COMMIT = re.compile(r"[a-f0-9]{40}")
_VERIFIER_ID = re.compile(r"[a-z][a-z0-9-]{0,63}")
_VERSION = re.compile(r"\d{1,6}\.\d{1,6}\.\d{1,6}")
_ISSUED_AT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


@dataclass(frozen=True)
class RepairCertificateVerification:
    schema_version: str
    certificate_sha256: str
    verifier_id: str
    snapshot_sha256: str
    state: str
    test_count: int
    promotion_allowed: bool = False


@dataclass(frozen=True)
class RepairVerifierTrustStore:
    trusted_public_keys: Mapping[str, bytes]
    verifier_source_sha256: Mapping[str, str]


def _unique_fields(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Repair certificate contains duplicate fields")
        result[key] = value
    return result


def _object(raw: bytes) -> dict:
    value = json.loads(raw, object_pairs_hook=_unique_fields)
    if type(value) is not dict:
        raise ValueError("Repair certificate payload must be an object")
    return value


def load_repair_verifier_trust_store(
    raw: bytes, *, root_public_key: bytes
) -> RepairVerifierTrustStore:
    """Load verifier keys and source pins under a separately provisioned root key."""
    if (
        type(raw) is not bytes
        or not 1 <= len(raw) <= 16384
        or type(root_public_key) is not bytes
        or len(root_public_key) != 32
    ):
        raise ValueError("Repair verifier trust inputs are invalid")
    try:
        envelope = _object(raw)
        if (
            set(envelope) != {"store", "signature"}
            or type(envelope["store"]) is not dict
            or set(envelope["store"]) != {"schema_version", "signers"}
            or envelope["store"]["schema_version"] != "1.0"
            or canonical_json(envelope).encode() != raw
            or type(envelope["signature"]) is not str
            or not re.fullmatch(r"[A-Za-z0-9_-]{86}", envelope["signature"])
        ):
            raise ValueError("Repair verifier trust store is not canonical or complete")
        signature = base64.urlsafe_b64decode(envelope["signature"] + "==")
        if base64.urlsafe_b64encode(signature).decode().rstrip("=") != envelope["signature"]:
            raise ValueError("Repair verifier trust signature encoding is invalid")
        try:
            Ed25519PublicKey.from_public_bytes(root_public_key).verify(
                signature, canonical_json(envelope["store"]).encode()
            )
        except (InvalidSignature, ValueError) as error:
            raise ValueError("Repair verifier trust root signature is invalid") from error
        signers = envelope["store"]["signers"]
        if type(signers) is not list or not 1 <= len(signers) <= 32:
            raise ValueError("Repair verifier trust inventory is invalid")
        keys, sources, identities = {}, {}, []
        for signer in signers:
            if (
                type(signer) is not dict
                or set(signer) != {"verifier_id", "public_key", "verifier_source_sha256"}
                or type(signer["verifier_id"]) is not str
                or not _VERIFIER_ID.fullmatch(signer["verifier_id"])
                or type(signer["public_key"]) is not str
                or not re.fullmatch(r"[A-Za-z0-9_-]{43}", signer["public_key"])
                or type(signer["verifier_source_sha256"]) is not str
                or not _DIGEST.fullmatch(signer["verifier_source_sha256"])
            ):
                raise ValueError("Repair verifier trust entry is invalid")
            public_bytes = base64.urlsafe_b64decode(signer["public_key"] + "=")
            if (
                len(public_bytes) != 32
                or base64.urlsafe_b64encode(public_bytes).decode().rstrip("=")
                != signer["public_key"]
            ):
                raise ValueError("Repair verifier public key encoding is invalid")
            verifier_id = signer["verifier_id"]
            identities.append(verifier_id)
            keys[verifier_id] = public_bytes
            sources[verifier_id] = signer["verifier_source_sha256"]
        if identities != sorted(set(identities)):
            raise ValueError("Repair verifier trust entries must be uniquely sorted")
        return RepairVerifierTrustStore(
            trusted_public_keys=MappingProxyType(keys),
            verifier_source_sha256=MappingProxyType(sources),
        )
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, KeyError, OverflowError) as error:
        raise ValueError("Repair verifier trust store is invalid") from error


def _validate_test_results(value: object, verdict: object) -> int:
    if type(value) is not list or not 1 <= len(value) <= 128:
        raise ValueError("Repair certificate requires bounded test results")
    identifiers = []
    failures = 0
    for item in value:
        if (
            type(item) is not dict
            or set(item) != {"test_id", "status", "evidence_sha256"}
            or type(item["test_id"]) is not str
            or not _TEST_ID.fullmatch(item["test_id"])
            or item["status"] not in {"PASS", "FAIL"}
            or type(item["status"]) is not str
            or type(item["evidence_sha256"]) is not str
            or not _DIGEST.fullmatch(item["evidence_sha256"])
        ):
            raise ValueError("Repair certificate test result is invalid")
        identifiers.append(item["test_id"])
        failures += item["status"] == "FAIL"
    if identifiers != sorted(set(identifiers)):
        raise ValueError("Repair certificate test results must be uniquely sorted")
    if (verdict == "PASS" and failures != 0) or (verdict == "FAIL" and failures == 0):
        raise ValueError("Repair certificate verdict conflicts with its test results")
    if verdict not in {"PASS", "FAIL"}:
        raise ValueError("Repair certificate verdict is invalid")
    return len(value)


def _version(value: str) -> tuple[int, int, int]:
    if type(value) is not str or not _VERSION.fullmatch(value):
        raise ValueError("Repair certificate version is invalid")
    return tuple(int(part) for part in value.split("."))


def verify_repair_certificate(
    snapshot: RepairSourceSnapshot,
    raw: bytes,
    *,
    trusted_public_keys: dict[str, bytes],
    replay_sha256: str,
    suite_sha256: str,
    request_sha256: str,
    incident_id: str,
    verifier_source_sha256: str,
    image_id: str,
) -> RepairCertificateVerification:
    """Verify signature, trust key, exact candidate/replay/tool pins, and test results.

    Trust keys must be provisioned by the caller through an independently controlled
    configuration channel. A valid signature attests the submitted evidence only; it
    never authorizes promotion or changes the health journal.
    """
    if (
        type(snapshot) is not RepairSourceSnapshot
        or type(raw) is not bytes
        or not 1 <= len(raw) <= 65536
        or not isinstance(trusted_public_keys, Mapping)
        or type(replay_sha256) is not str
        or not _DIGEST.fullmatch(replay_sha256)
        or type(suite_sha256) is not str
        or not _DIGEST.fullmatch(suite_sha256)
        or type(request_sha256) is not str
        or not _DIGEST.fullmatch(request_sha256)
        or type(incident_id) is not str
        or not _DIGEST.fullmatch(incident_id)
        or type(verifier_source_sha256) is not str
        or not _DIGEST.fullmatch(verifier_source_sha256)
        or type(image_id) is not str
        or not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id)
    ):
        raise ValueError("Repair certificate verification inputs are invalid")
    try:
        envelope = _object(raw)
        if set(envelope) != {"certificate", "signature"}:
            raise ValueError("Repair certificate envelope fields are invalid")
        certificate = envelope["certificate"]
        signature_text = envelope["signature"]
        if (
            type(certificate) is not dict
            or set(certificate) != _CERTIFICATE_FIELDS
            or type(signature_text) is not str
            or not re.fullmatch(r"[A-Za-z0-9_-]{86}", signature_text)
            or canonical_json(envelope).encode() != raw
        ):
            raise ValueError("Repair certificate envelope is not canonical or complete")
        verifier_id = certificate["verifier_id"]
        public_bytes = trusted_public_keys.get(verifier_id)
        if (
            type(verifier_id) is not str
            or not _VERIFIER_ID.fullmatch(verifier_id)
            or type(public_bytes) is not bytes
            or len(public_bytes) != 32
        ):
            raise ValueError("Repair certificate signer is not trusted")
        signature = base64.urlsafe_b64decode(signature_text + "==")
        if base64.urlsafe_b64encode(signature).decode().rstrip("=") != signature_text:
            raise ValueError("Repair certificate signature encoding is invalid")
        certificate_bytes = canonical_json(certificate).encode()
        try:
            Ed25519PublicKey.from_public_bytes(public_bytes).verify(signature, certificate_bytes)
        except (InvalidSignature, ValueError) as error:
            raise ValueError("Repair certificate signature is invalid") from error

        manifest = snapshot.proposed_manifest
        incident = snapshot.incident
        expected = {
            "schema_version": "1.0",
            "candidate_id": snapshot.candidate_id,
            "candidate_commit": snapshot.candidate_commit,
            "rollback_commit": snapshot.rollback_commit,
            "snapshot_sha256": snapshot.snapshot_sha256,
            "request_sha256": request_sha256,
            "incident_id": incident_id,
            "agent_id": manifest.get("agent_id"),
            "old_version": incident.get("agent_version"),
            "new_version": manifest.get("agent_version"),
            "replay_sha256": replay_sha256,
            "suite_sha256": suite_sha256,
            "verifier_source_sha256": verifier_source_sha256,
            "image_id": image_id,
            "promotion_allowed": False,
        }
        if any(certificate.get(key) != value for key, value in expected.items()):
            raise ValueError("Repair certificate does not bind the exact candidate and verifier")
        if (
            type(certificate["promotion_allowed"]) is not bool
            or certificate["promotion_allowed"] is not False
            or type(certificate["agent_id"]) is not str
            or type(certificate["old_version"]) is not str
            or type(certificate["new_version"]) is not str
            or not _VERSION.fullmatch(certificate["old_version"])
            or not _VERSION.fullmatch(certificate["new_version"])
            or type(certificate["issued_at"]) is not str
            or not _ISSUED_AT.fullmatch(certificate["issued_at"])
            or type(certificate["verifier_source_sha256"]) is not str
            or not _DIGEST.fullmatch(certificate["verifier_source_sha256"])
            or type(certificate["suite_sha256"]) is not str
            or not _DIGEST.fullmatch(certificate["suite_sha256"])
            or type(certificate["replay_sha256"]) is not str
            or not _DIGEST.fullmatch(certificate["replay_sha256"])
            or type(certificate["image_id"]) is not str
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", certificate["image_id"])
            or any(
                type(certificate[key]) is not str or not pattern.fullmatch(certificate[key])
                for key, pattern in (
                    ("candidate_id", _DIGEST),
                    ("snapshot_sha256", _DIGEST),
                    ("incident_id", _DIGEST),
                    ("candidate_commit", _GIT_COMMIT),
                    ("rollback_commit", _GIT_COMMIT),
                )
            )
        ):
            raise ValueError("Repair certificate identity or timestamp is invalid")
        if _version(certificate["new_version"]) <= _version(certificate["old_version"]):
            raise ValueError("Repair certificate must identify a new candidate version")
        datetime.strptime(certificate["issued_at"], "%Y-%m-%dT%H:%M:%SZ")
        count = _validate_test_results(certificate["test_results"], certificate["verdict"])
        state = (
            "INDEPENDENT_VERIFICATION_PASSED"
            if certificate["verdict"] == "PASS"
            else "REPAIR_FAILED_AWAITING_REVIEW"
        )
        return RepairCertificateVerification(
            schema_version="1.0",
            certificate_sha256=sha256(raw).hexdigest(),
            verifier_id=verifier_id,
            snapshot_sha256=snapshot.snapshot_sha256,
            state=state,
            test_count=count,
        )
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, KeyError, OverflowError) as error:
        raise ValueError("Repair certificate is invalid") from error
