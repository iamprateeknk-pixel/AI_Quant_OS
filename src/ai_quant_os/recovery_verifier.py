"""Independent Recovery Verification (V3.2 blueprint section 13).

After an agent failure the platform freezes new risk first (deterministic
watchdog, never the LLM repair agent), quarantines the failed agent, repairs
it as a new immutable candidate in a sandbox, and only resumes trading
through the staged state machine below. There is no direct
frozen -> NORMAL jump: every frozen state (NO_NEW_RISK or HALT) must travel
snapshot -> quarantine -> diagnose -> repair -> test/replay/eval ->
shadow/canary -> verified -> REDUCED_RISK before NORMAL is reachable.

The verifier in this module is independent of the Repair Agent by
construction: it shares no state with it, and a recovery whose only support
is the repair agent's own self-assessment is rejected — an independent
incident replay must also have passed (``independent_replay_passed``).
"""

from dataclasses import dataclass
from enum import Enum


class RecoveryState(str, Enum):
    """Recovery state machine (section 13), in lifecycle order."""

    NORMAL = "NORMAL"
    FAULT_DETECTED = "FAULT_DETECTED"
    NO_NEW_RISK = "NO_NEW_RISK"
    HALT = "HALT"
    STATE_SNAPSHOT = "STATE_SNAPSHOT"
    AGENT_QUARANTINED = "AGENT_QUARANTINED"
    DIAGNOSE = "DIAGNOSE"
    REPAIR_CANDIDATE = "REPAIR_CANDIDATE"
    TEST_REPLAY_EVAL = "TEST_REPLAY_EVAL"
    SHADOW_CANARY = "SHADOW_CANARY"
    RECOVERY_VERIFIED = "RECOVERY_VERIFIED"
    REDUCED_RISK = "REDUCED_RISK"


#: Allowed next states. NORMAL is reachable only from REDUCED_RISK, so no
#: frozen state (NO_NEW_RISK / HALT) and not even RECOVERY_VERIFIED can jump
#: straight back to NORMAL. Failed eval returns to DIAGNOSE for a new repair
#: candidate; a canary regression re-quarantines the agent; a regression
#: during REDUCED_RISK re-freezes to NO_NEW_RISK.
TRANSITIONS: dict[RecoveryState, tuple[RecoveryState, ...]] = {
    RecoveryState.NORMAL: (RecoveryState.FAULT_DETECTED,),
    RecoveryState.FAULT_DETECTED: (RecoveryState.NO_NEW_RISK, RecoveryState.HALT),
    RecoveryState.NO_NEW_RISK: (RecoveryState.STATE_SNAPSHOT, RecoveryState.HALT),
    RecoveryState.HALT: (RecoveryState.STATE_SNAPSHOT,),
    RecoveryState.STATE_SNAPSHOT: (RecoveryState.AGENT_QUARANTINED,),
    RecoveryState.AGENT_QUARANTINED: (RecoveryState.DIAGNOSE,),
    RecoveryState.DIAGNOSE: (RecoveryState.REPAIR_CANDIDATE,),
    RecoveryState.REPAIR_CANDIDATE: (RecoveryState.TEST_REPLAY_EVAL,),
    RecoveryState.TEST_REPLAY_EVAL: (RecoveryState.SHADOW_CANARY, RecoveryState.DIAGNOSE),
    RecoveryState.SHADOW_CANARY: (RecoveryState.RECOVERY_VERIFIED, RecoveryState.AGENT_QUARANTINED),
    RecoveryState.RECOVERY_VERIFIED: (RecoveryState.REDUCED_RISK,),
    RecoveryState.REDUCED_RISK: (RecoveryState.NORMAL, RecoveryState.NO_NEW_RISK),
}


def advance(current: RecoveryState, target: RecoveryState) -> RecoveryState:
    """Move the recovery machine, raising ValueError on any illegal jump."""
    if target not in TRANSITIONS.get(current, ()):
        raise ValueError(f"Illegal recovery transition: {current.value} -> {target.value}")
    return target


#: The eight recovery-quorum gates (section 13). All must pass before the
#: resume controller may advance a verified recovery to REDUCED_RISK.
QUORUM_GATES = (
    "agent_health",
    "repair_verification",
    "data_state",
    "risk_execution",
    "broker_truth",
    "security",
    "canary",
    "resume_controller",
)


@dataclass(frozen=True)
class VerificationReport:
    """Verifier verdict: VERIFIED only when the full quorum is satisfied.

    ``failed_gates`` names every quorum gate that was missing or not True,
    plus the pseudo-gates ``independent_replay`` (no independent incident
    replay passed) and ``evidence`` (no evidence references supplied) when
    those independence/evidence requirements fail.
    """

    verdict: str
    failed_gates: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.verdict not in ("VERIFIED", "REJECTED"):
            raise ValueError(f"Unknown verification verdict: {self.verdict!r}")


def verify_recovery(
    *,
    gate_results: dict[str, bool],
    evidence_refs: tuple[str, ...],
    independent_replay_passed: bool,
) -> VerificationReport:
    """Verify a repaired agent against the full recovery quorum.

    VERIFIED requires every one of the eight QUORUM_GATES to be present in
    ``gate_results`` with value True, at least one evidence reference, and
    ``independent_replay_passed`` True. Gate entries for names outside
    QUORUM_GATES are ignored. In particular, a repair agent's own
    self-assessment — even with every gate claimed True — is REJECTED when
    no independent replay passed.
    """
    failed = tuple(gate for gate in QUORUM_GATES if gate_results.get(gate) is not True)
    if not independent_replay_passed:
        failed += ("independent_replay",)
    if not evidence_refs:
        failed += ("evidence",)
    return VerificationReport(
        verdict="VERIFIED" if not failed else "REJECTED",
        failed_gates=failed,
        evidence_refs=tuple(evidence_refs),
    )
