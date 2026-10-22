"""Strategy promotion gate: Validation Fortress plus council decision.

A strategy may be promoted only when two independent legs both pass:

1. The Validation Fortress leg -- a :class:`FortressVerdict` must be
   present, it must have been issued for the very hypothesis named in
   the promotion request, and it must have passed.
2. The council leg -- the strategy-owning council must accept the
   proposal under the material decision protocol in
   :mod:`ai_quant_os.councils`.

Both legs are mandatory and each one independently blocks promotion:
a passing fortress verdict never overrides a council rejection or
escalation, and a council acceptance never overrides a missing,
mismatched or failed fortress verdict.

Council choice: ``councils.py`` defines no council named ``"strategy"``.
Its ``"research"`` council -- hypothesis, feature, strategy_designer,
statistician and skeptic -- is the council that owns strategy design,
so this gate routes every promotion proposal to ``"research"``
(:data:`STRATEGY_COUNCIL`).

Lifecycle status: :func:`evaluate_promotion` is the decision function
a future lifecycle hook must call before a strategy becomes
paper-active. Today no lifecycle hook consumes a promotion decision
-- the existing promotion contracts keep ``approved_by_policy`` and
``promotion_allowed`` pinned to ``False`` -- so this module changes no
runtime behaviour. It only computes and records the decision such a
hook would have to honour.
"""

from dataclasses import dataclass

from ai_quant_os.councils import CouncilDecision, run_council_decision
from ai_quant_os.validation_fortress import FortressVerdict

#: The council that owns strategy promotion decisions. There is no
#: council named "strategy" in councils.py; the "research" council
#: (hypothesis, feature, strategy_designer, statistician, skeptic) is
#: the strategy-owning council.
STRATEGY_COUNCIL = "research"


@dataclass(frozen=True)
class PromotionRequest:
    """Everything the promotion gate needs to decide one promotion.

    ``fortress_verdict`` is the Validation Fortress verdict for the
    candidate and ``fortress_hypothesis_id`` is the hypothesis that
    verdict was issued for; the gate only honours the verdict when that
    id matches ``hypothesis_id``. The remaining fields are the council
    protocol inputs: who proposes the promotion, any unresolved
    challenger objections, the verifier and policy outcomes, a hard
    fail flag, and the human-approval state.
    """

    strategy_id: str
    hypothesis_id: str
    fortress_verdict: FortressVerdict | None = None
    fortress_hypothesis_id: str | None = None
    proposer: str = "strategy-promotion-gate"
    challenger_objections: tuple[str, ...] = ()
    verifier_passed: bool = False
    policy_passed: bool = False
    hard_fail: bool = False
    human_approval: bool | None = None
    requires_human: bool = False

    def __post_init__(self) -> None:
        if not self.strategy_id:
            raise ValueError("strategy_id is required")
        if not self.hypothesis_id:
            raise ValueError("hypothesis_id is required")


@dataclass(frozen=True)
class PromotionGateDecision:
    """The immutable outcome of one promotion evaluation.

    ``promoted`` is true only when both legs passed. ``fortress_passed``
    records the fortress leg on its own, ``council_verdict`` and
    ``council_decision`` record the council leg, and ``reasons`` lists
    every blocking reason (empty exactly when ``promoted`` is true).
    """

    strategy_id: str
    hypothesis_id: str
    promoted: bool
    fortress_passed: bool
    council_verdict: str | None
    council_decision: CouncilDecision | None
    reasons: tuple[str, ...]


def evaluate_promotion(request: PromotionRequest) -> PromotionGateDecision:
    """Decide whether ``request``'s strategy may be promoted.

    The fortress leg passes only when a verdict is present, was issued
    for ``request.hypothesis_id`` and passed. The council leg always
    runs -- even when the fortress leg already failed -- so the decision
    records what the strategy council thought of the proposal, and it
    passes only on an ``ACCEPTED`` verdict. Promotion requires both.
    """
    reasons: list[str] = []

    verdict = request.fortress_verdict
    if verdict is None:
        fortress_ok = False
        reasons.append("no fortress verdict")
    elif request.fortress_hypothesis_id != request.hypothesis_id:
        fortress_ok = False
        reasons.append("fortress verdict was issued for a different hypothesis")
    elif not verdict.passed:
        fortress_ok = False
        reasons.append(f"fortress verdict is FAIL: {verdict.summary()}")
    else:
        fortress_ok = True

    council_decision = run_council_decision(
        STRATEGY_COUNCIL,
        proposal_id=request.strategy_id,
        proposer=request.proposer,
        challenger_objections=request.challenger_objections,
        verifier_passed=request.verifier_passed,
        policy_passed=request.policy_passed,
        hard_fail=request.hard_fail,
        human_approval=request.human_approval,
        requires_human=request.requires_human,
    )
    council_ok = council_decision.verdict == "ACCEPTED"
    if not council_ok:
        reasons.append(
            f"strategy council verdict {council_decision.verdict}: {council_decision.rationale}"
        )

    promoted = fortress_ok and council_ok
    return PromotionGateDecision(
        strategy_id=request.strategy_id,
        hypothesis_id=request.hypothesis_id,
        promoted=promoted,
        fortress_passed=fortress_ok,
        council_verdict=council_decision.verdict,
        council_decision=council_decision,
        reasons=tuple(reasons),
    )


__all__ = [
    "STRATEGY_COUNCIL",
    "PromotionGateDecision",
    "PromotionRequest",
    "evaluate_promotion",
]
