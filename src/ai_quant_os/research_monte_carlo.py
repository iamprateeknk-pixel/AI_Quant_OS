"""Bind closed-trade resampling to a registered native holdout result."""

from dataclasses import dataclass
from decimal import Context, Decimal, localcontext
from hashlib import sha256
import json

from pydantic import TypeAdapter

from ai_quant_os.monte_carlo import (
    CashFill,
    MonteCarloResult,
    MonteCarloSpec,
    closed_trade_series,
    run_monte_carlo,
)
from ai_quant_os.registered_backtest import RegisteredBacktest


@dataclass(frozen=True)
class NativeMonteCarlo:
    native_result_sha256: str
    dataset_fingerprint: str
    genome_sha256: str
    implementation_sha256: str
    pnl_currency: str
    result: MonteCarloResult


def evaluate_native_monte_carlo(run: RegisteredBacktest, spec: MonteCarloSpec) -> NativeMonteCarlo:
    with localcontext(Context(prec=28)):
        if run.result.pnl_currency != spec.risk_currency:
            raise ValueError("Monte Carlo drawdown currency differs from native result")
        fills = tuple(
            CashFill(row.side, row.quantity, row.price, row.fee) for row in run.result.fills
        )
        series = closed_trade_series(fills)
        if series.open_position != run.result.final_position:
            raise ValueError("Monte Carlo trade ledger differs from native position")
        if not series.open_position and sum(series.net_pnls, Decimal(0)) != run.result.net_pnl:
            raise ValueError("Monte Carlo closed-trade ledger differs from native P&L")
        record = json.loads(TypeAdapter(type(run.result)).dump_json(run.result))
        raw = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        return NativeMonteCarlo(
            sha256(raw).hexdigest(),
            run.result.dataset_fingerprint,
            run.binding.genome_sha256,
            run.binding.implementation_sha256,
            run.result.pnl_currency,
            run_monte_carlo(series, spec),
        )
