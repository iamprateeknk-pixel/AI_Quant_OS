"""Agent councils and the material decision protocol (V3.2 section 7).

V3.2 avoids single-agent authority for material research or allocation
decisions. Critical outputs are produced by councils whose members hold
intentionally different roles, and every material decision follows the same
protocol:

    PROPOSER -> EVIDENCE PACKAGE -> CHALLENGER -> VERIFIER -> POLICY CHECK
    -> (optional HUMAN APPROVAL) -> DETERMINISTIC SERVICE / REGISTRY UPDATE

This module models only the decision step. It is deterministic, stdlib
only, and holds no trading authority: an ``ACCEPTED`` verdict means the
proposal may proceed to the deterministic service that owns the state
change, never that this module performs it.

Verdict rules, in evaluation order:

1. A hard fail (for example a validation hard-fail agent rejecting a
   candidate) rejects the proposal outright.
2. A failed verifier rejects the proposal.
3. A failed policy check rejects the proposal.
4. Unresolved challenger objections escalate the proposal for a human
   decision, unless a human has already approved it.
5. A council marked ``requires_human`` escalates unless a human has
   explicitly approved.
6. Otherwise the proposal is accepted.
"""

from dataclasses import dataclass

#: Council membership by role, exactly as section 7 defines each council.
COUNCILS: dict[str, tuple[str, ...]] = {
    "market_state": (
        "regime",
        "macro",
        "rates",
        "volatility",
        "liquidity",
        "event",
    ),
    "research": (
        "hypothesis",
        "feature",
        "strategy_designer",
        "statistician",
        "skeptic",
    ),
    "validation": (
        "oos",
        "walk_forward",
        "monte_carlo",
        "leakage",
        "multiple_testing",
        "capacity",
        "red_team",
    ),
    "portfolio": (
        "tournament",
        "correlation",
        "allocation",
        "drawdown",
        "capacity",
    ),
    "execution": (
        "execution_planner",
        "liquidity",
        "tca",
        "broker_health",
    ),
    "incident": (
        "broker_health",
        "data_quality",
        "reconciliation",
        "security",
        "incident_commander",
    ),
}

VERDICTS = ("ACCEPTED", "REJECTED", "ESCALATED")


@dataclass(frozen=True)
class CouncilDecision:
    """The immutable outcome of one council decision."""

    council: str
    proposal_id: str
    verdict: str
    proposer: str
    challenger_objections: tuple[str, ...] = ()
    verifier_passed: bool = False
    policy_passed: bool = False
    hard_fail: bool = False
    rationale: str = ""

    def __post_init__(self) -> None:
        if self.council not in COUNCILS:
            raise ValueError(f"Unknown council: {self.council}")
        if self.verdict not in VERDICTS:
            raise ValueError(f"Verdict must be one of {VERDICTS}")
        if not self.proposal_id:
            raise ValueError("proposal_id is required")
        if not self.proposer:
            raise ValueError("proposer is required")


def run_council_decision(
    council: str,
    proposal_id: str,
    *,
    proposer: str,
    challenger_objections: tuple[str, ...] | list[str] = (),
    verifier_passed: bool,
    policy_passed: bool,
    hard_fail: bool = False,
    human_approval: bool | None = None,
    requires_human: bool = False,
) -> CouncilDecision:
    """Run the section 7 protocol for one proposal and return its verdict.

    ``challenger_objections`` are objections the challenger raised that the
    evidence package did not resolve. They do not reject a proposal on
    their own -- rejection is reserved for hard fails, the verifier and
    policy -- but unresolved objections always escalate to a human unless
    that human has already approved (``human_approval is True``).
    """
    if council not in COUNCILS:
        raise ValueError(f"Unknown council: {council}")
    if not proposal_id:
        raise ValueError("proposal_id is required")
    if not proposer:
        raise ValueError("proposer is required")
    objections = tuple(str(item) for item in challenger_objections)

    def decide(verdict: str, rationale: str) -> CouncilDecision:
        return CouncilDecision(
            council=council,
            proposal_id=proposal_id,
            verdict=verdict,
            proposer=proposer,
            challenger_objections=objections,
            verifier_passed=bool(verifier_passed),
            policy_passed=bool(policy_passed),
            hard_fail=bool(hard_fail),
            rationale=rationale,
        )

    if hard_fail:
        return decide("REJECTED", "Hard fail raised; proposal rejected outright.")
    if not verifier_passed:
        return decide("REJECTED", "Verifier did not pass the evidence package.")
    if not policy_passed:
        return decide("REJECTED", "Policy check did not pass.")
    if objections and human_approval is not True:
        return decide(
            "ESCALATED",
            "Unresolved challenger objections require a human decision.",
        )
    if requires_human and human_approval is not True:
        return decide(
            "ESCALATED",
            "This decision requires human approval, which has not been given.",
        )
    return decide("ACCEPTED", "Challenger, verifier and policy checks passed.")


__all__ = [
    "COUNCILS",
    "VERDICTS",
    "CouncilDecision",
    "run_council_decision",
]
