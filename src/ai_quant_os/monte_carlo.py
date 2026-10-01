"""Deterministic circular block resampling of completed trade cash flows."""

from dataclasses import dataclass
from decimal import Context, Decimal, Inexact, localcontext
from hashlib import sha256
import json
from typing import Literal

REVERSAL_MONTE_CARLO_METHOD = "CIRCULAR_EQUAL_REVERSAL_HALF_FEE_BLOCKS_SHA256_V1"


@dataclass(frozen=True)
class CashFill:
    side: Literal["BUY", "SELL"]
    quantity: Decimal
    price: Decimal
    fee: Decimal

    def __post_init__(self):
        if (
            self.side not in ("BUY", "SELL")
            or any(
                type(v) is not Decimal or not v.is_finite()
                for v in (self.quantity, self.price, self.fee)
            )
            or self.quantity <= 0
            or self.price <= 0
            or self.fee < 0
        ):
            raise ValueError("Invalid trade cash flow")


@dataclass(frozen=True)
class ClosedTradeSeries:
    net_pnls: tuple[Decimal, ...]
    open_position: Decimal

    def __post_init__(self):
        if (
            type(self.net_pnls) is not tuple
            or len(self.net_pnls) > 4096
            or any(type(v) is not Decimal or not v.is_finite() for v in self.net_pnls)
            or type(self.open_position) is not Decimal
            or not self.open_position.is_finite()
        ):
            raise ValueError("Invalid closed-trade sample")

    @property
    def fingerprint(self):
        with localcontext(Context(prec=28)):
            raw = json.dumps(
                [[str(v.normalize()) for v in self.net_pnls], str(self.open_position.normalize())],
                separators=(",", ":"),
            ).encode()
            return sha256(raw).hexdigest()


def closed_trade_series(
    fills: tuple[CashFill, ...],
    *,
    reversal_fee_policy: Literal["REJECT", "EQUAL_SIZE_HALF_FEE_V1"] = "REJECT",
) -> ClosedTradeSeries:
    if reversal_fee_policy not in ("REJECT", "EQUAL_SIZE_HALF_FEE_V1"):
        raise ValueError("Unknown reversal fee policy")
    if len(fills) > 4096:
        raise ValueError("Trade cash-flow capacity exceeded")
    context = Context(prec=28)
    if reversal_fee_policy == "EQUAL_SIZE_HALF_FEE_V1":
        context.traps[Inexact] = True
    with localcontext(context):
        position, cash, completed = Decimal(0), Decimal(0), []
        for fill in fills:
            delta = fill.quantity if fill.side == "BUY" else -fill.quantity
            if position and position * delta < 0 and abs(delta) > abs(position):
                if reversal_fee_policy == "REJECT":
                    raise ValueError("Position reversal requires explicit fee attribution")
                if abs(delta) != 2 * abs(position):
                    raise ValueError("Half-fee attribution requires equal reversal legs")
                # One native order closes Q and opens Q; each leg owns half its fee.
                closing_delta = -position
                closing_fee = fill.fee / 2
                completed.append(cash - closing_delta * fill.price - closing_fee)
                position = delta + position
                cash = -position * fill.price - (fill.fee - closing_fee)
                continue
            cash += (
                -fill.price * fill.quantity if fill.side == "BUY" else fill.price * fill.quantity
            ) - fill.fee
            position += delta
            if position == 0:
                completed.append(cash)
                cash = Decimal(0)
        return ClosedTradeSeries(tuple(completed), position)


@dataclass(frozen=True)
class MonteCarloSpec:
    seed: int = 17
    iterations: int = 500
    block_size: int = 4
    min_closed_trades: int = 20
    lower_tail_percent: int = 5
    minimum_positive_fraction: Decimal = Decimal("0.95")
    drawdown_limit: Decimal = Decimal(100)
    risk_currency: Literal["USD", "JPY"] = "USD"
    method: Literal[
        "CIRCULAR_CLOSED_TRADE_BLOCKS_SHA256_V1",
        "CIRCULAR_EQUAL_REVERSAL_HALF_FEE_BLOCKS_SHA256_V1",
    ] = "CIRCULAR_CLOSED_TRADE_BLOCKS_SHA256_V1"

    def __post_init__(self):
        for value, low, high in (
            (self.seed, 0, 2**32 - 1),
            (self.iterations, 100, 2000),
            (self.block_size, 1, 32),
            (self.min_closed_trades, 20, 4096),
            (self.lower_tail_percent, 1, 25),
        ):
            if type(value) is not int or not low <= value <= high:
                raise ValueError("Invalid Monte Carlo integer policy")
        if (
            any(
                type(v) is not Decimal or not v.is_finite()
                for v in (self.minimum_positive_fraction, self.drawdown_limit)
            )
            or not 0 <= self.minimum_positive_fraction <= 1
            or self.drawdown_limit < 0
            or self.risk_currency not in ("USD", "JPY")
            or self.method
            not in ("CIRCULAR_CLOSED_TRADE_BLOCKS_SHA256_V1", REVERSAL_MONTE_CARLO_METHOD)
        ):
            raise ValueError("Invalid Monte Carlo policy")


@dataclass(frozen=True)
class MonteCarloResult:
    spec: MonteCarloSpec
    sample_fingerprint: str
    closed_trade_count: int
    open_position: Decimal
    state: Literal["PASS", "FAIL", "PENDING"]
    reason: str
    lower_tail_pnl: Decimal | None
    median_pnl: Decimal | None
    drawdown_p95: Decimal | None
    positive_fraction: Decimal | None
    simulation_sha256: str | None
    drawdown_basis: Literal["TRADE_CLOSE_CASHFLOW"] = "TRADE_CLOSE_CASHFLOW"


class _Indices:
    def __init__(self, seed):
        self.seed, self.counter = seed, 0

    def draw(self, size):
        cutoff = (2**256 // size) * size
        while True:
            raw = (
                b"AI_QUANT_MC_CBB_V1"
                + self.seed.to_bytes(8, "big")
                + self.counter.to_bytes(8, "big")
            )
            self.counter += 1
            value = int.from_bytes(sha256(raw).digest(), "big")
            if value < cutoff:
                return value % size


def run_monte_carlo(series: ClosedTradeSeries, spec: MonteCarloSpec) -> MonteCarloResult:
    count = len(series.net_pnls)
    reason = (
        "UNCLOSED_POSITION"
        if series.open_position
        else "INSUFFICIENT_CLOSED_TRADES"
        if count < max(spec.min_closed_trades, spec.block_size)
        else None
    )
    if reason:
        return MonteCarloResult(
            spec,
            series.fingerprint,
            count,
            series.open_position,
            "PENDING",
            reason,
            None,
            None,
            None,
            None,
            None,
        )
    if count * spec.iterations > 500000:
        raise ValueError("Monte Carlo computation capacity exceeded")
    with localcontext(Context(prec=28)):
        indices, outcomes, drawdowns = _Indices(spec.seed), [], []
        for _ in range(spec.iterations):
            equity, peak, drawdown, used = Decimal(0), Decimal(0), Decimal(0), 0
            while used < count:
                start = indices.draw(count)
                for offset in range(min(spec.block_size, count - used)):
                    equity += series.net_pnls[(start + offset) % count]
                    peak = max(peak, equity)
                    drawdown = max(drawdown, peak - equity)
                    used += 1
            outcomes.append(equity)
            drawdowns.append(drawdown)
        ordered, ordered_dd = sorted(outcomes), sorted(drawdowns)
        lower = ordered[(spec.lower_tail_percent * spec.iterations + 99) // 100 - 1]
        median = ordered[(50 * spec.iterations + 99) // 100 - 1]
        tail_dd = ordered_dd[(95 * spec.iterations + 99) // 100 - 1]
        positive = Decimal(sum(value > 0 for value in outcomes)) / spec.iterations
        passed = (
            lower >= 0
            and tail_dd <= spec.drawdown_limit
            and positive >= spec.minimum_positive_fraction
        )
        raw = json.dumps(
            [[str(p.normalize()), str(d.normalize())] for p, d in zip(outcomes, drawdowns)],
            separators=(",", ":"),
        ).encode()
        return MonteCarloResult(
            spec,
            series.fingerprint,
            count,
            series.open_position,
            "PASS" if passed else "FAIL",
            "DECLARED_SCREEN_PASSED" if passed else "DECLARED_SCREEN_FAILED",
            lower,
            median,
            tail_dd,
            positive,
            sha256(raw).hexdigest(),
        )
