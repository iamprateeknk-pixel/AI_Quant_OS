from dataclasses import dataclass
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
import re
from typing import Literal

from ai_quant_os.shadow_portfolio import ShadowReport

SCALE = Decimal("0.000001")


@dataclass(frozen=True)
class RealityGapPolicy:
    minimum_shadow_bars: int
    minimum_shadow_fills: int
    minimum_holdout_fills: int
    adverse_gap_threshold_bps: Decimal

    def __post_init__(self):
        threshold = self.adverse_gap_threshold_bps
        if (
            type(self.minimum_shadow_bars) is not int
            or not 2 <= self.minimum_shadow_bars <= 4096
            or type(self.minimum_shadow_fills) is not int
            or not 1 <= self.minimum_shadow_fills <= 512
            or type(self.minimum_holdout_fills) is not int
            or not 1 <= self.minimum_holdout_fills <= 512
            or type(threshold) is not Decimal
            or not threshold.is_finite()
            or not SCALE <= threshold <= 10000
            or len(threshold.as_tuple().digits) > 16
            or not -6 <= threshold.as_tuple().exponent <= 6
        ):
            raise ValueError("Invalid declared shadow reality-gap policy")
        with localcontext(Context(prec=80)):
            object.__setattr__(
                self,
                "adverse_gap_threshold_bps",
                threshold.quantize(SCALE, rounding=ROUND_HALF_EVEN),
            )

    @property
    def content_ref(self):
        raw = (
            f"shadow-reality-gap-v1:{self.minimum_shadow_bars}:"
            f"{self.minimum_shadow_fills}:{self.minimum_holdout_fills}:"
            f"{self.adverse_gap_threshold_bps}"
        ).encode()
        from hashlib import sha256

        return sha256(raw).hexdigest()


@dataclass(frozen=True)
class RealityGapAssessment:
    candidate_id: str
    shadow_id: str
    report_sha256: str
    policy: RealityGapPolicy
    holdout_return_bps: Decimal | None
    shadow_return_bps: Decimal | None
    gap_bps: Decimal | None
    state: Literal["UNASSESSED", "WITHIN_THRESHOLD", "DEMOTION_REQUIRED"]
    reasons: tuple[str, ...]
    promotion_allowed: Literal[False] = False

    def __post_init__(self):
        if (
            not re.fullmatch(r"[a-f0-9]{64}", self.candidate_id)
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", self.shadow_id)
            or not re.fullmatch(r"[a-f0-9]{64}", self.report_sha256)
            or type(self.policy) is not RealityGapPolicy
            or any(
                value is not None and (type(value) is not Decimal or not value.is_finite())
                for value in (self.holdout_return_bps, self.shadow_return_bps, self.gap_bps)
            )
            or self.state not in ("UNASSESSED", "WITHIN_THRESHOLD", "DEMOTION_REQUIRED")
            or type(self.reasons) is not tuple
            or any(not isinstance(reason, str) for reason in self.reasons)
            or self.promotion_allowed is not False
            or (self.state == "UNASSESSED") != (self.gap_bps is None)
            or (self.state == "DEMOTION_REQUIRED")
            != (self.gap_bps is not None and self.gap_bps <= -self.policy.adverse_gap_threshold_bps)
            or (self.state == "UNASSESSED") != bool(self.reasons)
        ):
            raise ValueError("Invalid shadow reality-gap assessment")


def _return_bps(result):
    with localcontext(Context(prec=80)):
        gross = sum((fill.quantity * fill.price for fill in result.fills), Decimal(0))
        if gross <= 0:
            return None
        return (result.net_pnl / gross * Decimal(10000)).quantize(SCALE, rounding=ROUND_HALF_EVEN)


def assess_shadow_reality_gap(report, policy):
    if type(report) is not ShadowReport or type(policy) is not RealityGapPolicy:
        raise ValueError("Typed retained shadow evidence and declared policy required")
    candidate_id = report.assignment.candidate.manifest.candidate_id
    if report.outcome != "OBSERVED" or report.run is None:
        return RealityGapAssessment(
            candidate_id,
            report.assignment.shadow_id,
            report.fingerprint,
            policy,
            None,
            None,
            None,
            "UNASSESSED",
            ("SHADOW_RUN_NOT_OBSERVED",),
        )
    holdout = report.assignment.candidate.report.holdout.result
    shadow = report.run.result
    reasons = []
    if len(report.source_bars) < policy.minimum_shadow_bars:
        reasons.append("INSUFFICIENT_SHADOW_BARS")
    if len(holdout.fills) < policy.minimum_holdout_fills:
        reasons.append("INSUFFICIENT_HOLDOUT_FILLS")
    if len(shadow.fills) < policy.minimum_shadow_fills:
        reasons.append("INSUFFICIENT_SHADOW_FILLS")
    if holdout.pnl_currency != shadow.pnl_currency:
        reasons.append("PNL_CURRENCY_MISMATCH")
    holdout_return = _return_bps(holdout)
    shadow_return = _return_bps(shadow)
    if holdout_return is None or shadow_return is None:
        reasons.append("GROSS_NOTIONAL_UNAVAILABLE")
    if reasons:
        return RealityGapAssessment(
            candidate_id,
            report.assignment.shadow_id,
            report.fingerprint,
            policy,
            holdout_return,
            shadow_return,
            None,
            "UNASSESSED",
            tuple(sorted(set(reasons))),
        )
    gap = shadow_return - holdout_return
    return RealityGapAssessment(
        candidate_id,
        report.assignment.shadow_id,
        report.fingerprint,
        policy,
        holdout_return,
        shadow_return,
        gap,
        "DEMOTION_REQUIRED" if gap <= -policy.adverse_gap_threshold_bps else "WITHIN_THRESHOLD",
        (),
    )
