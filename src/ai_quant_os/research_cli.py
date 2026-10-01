"""Archived-data research runner with explicit development and terminal holdout inputs."""

import argparse
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

from ai_quant_os.backtest import BacktestCosts
from ai_quant_os.data_archive import BarArchive
from ai_quant_os.research_contracts import Hypothesis, ResearchDatasetScope, ResearchPlan
from ai_quant_os.strategy_contracts import StrategyGenome
from ai_quant_os.monte_carlo import MonteCarloSpec
from ai_quant_os.adversarial import AdversarialSpec
from ai_quant_os.directional_strategy import DIRECTIONAL_REF


def main():
    parser = argparse.ArgumentParser(
        description="Evidence-only native research; no promotion authority"
    )
    parser.add_argument("development", type=Path)
    parser.add_argument("holdout", type=Path)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--hypothesis-id", required=True)
    parser.add_argument("--description", required=True)
    parser.add_argument("--candidate", action="append", required=True, metavar="FAST:SLOW")
    parser.add_argument(
        "--strategy",
        choices=("moving-average-cross", "moving-average-directional"),
        default="moving-average-cross",
    )
    parser.add_argument("--train-bars", type=int, required=True)
    parser.add_argument("--validation-bars", type=int, required=True)
    parser.add_argument("--quantity", type=int, default=1)
    parser.add_argument("--slippage-bps", type=Decimal, default=Decimal(0))
    parser.add_argument("--fee", type=Decimal, default=Decimal(0))
    parser.add_argument("--stress-slippage-bps", type=Decimal, required=True)
    parser.add_argument("--stress-fee", type=Decimal, required=True)
    parser.add_argument("--monte-carlo", action="store_true")
    parser.add_argument("--mc-seed", type=int)
    parser.add_argument("--mc-iterations", type=int)
    parser.add_argument("--mc-block-size", type=int)
    parser.add_argument("--mc-drawdown-limit", type=Decimal)
    parser.add_argument("--mc-currency", choices=("USD", "JPY"))
    parser.add_argument("--adversarial", action="store_true")
    parser.add_argument("--adversarial-drop-every", type=int)
    parser.add_argument("--adversarial-shock-bps", type=int)
    parser.add_argument("--adversarial-loss-limit", type=Decimal)
    parser.add_argument("--adversarial-currency", choices=("USD", "JPY"))
    parser.add_argument("--workspace", type=Path, default=Path("data-local/research"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        from ai_quant_os.research_pipeline import run_research_experiment
        from ai_quant_os.research_store import ResearchStore, encode_research
        from ai_quant_os.strategy_registry import StrategyRegistry

        root, output = args.workspace.resolve(), args.output.resolve()
        mc_kwargs = {
            key: value
            for key, value in (
                ("seed", args.mc_seed),
                ("iterations", args.mc_iterations),
                ("block_size", args.mc_block_size),
                ("drawdown_limit", args.mc_drawdown_limit),
                ("risk_currency", args.mc_currency),
            )
            if value is not None
        }
        if mc_kwargs and not args.monte_carlo:
            raise ValueError("Monte Carlo options require --monte-carlo declaration")
        mc_spec = MonteCarloSpec(**mc_kwargs) if args.monte_carlo else None
        adverse_kwargs = {
            key: value
            for key, value in (
                ("drop_every", args.adversarial_drop_every),
                ("shock_bps", args.adversarial_shock_bps),
                ("maximum_loss", args.adversarial_loss_limit),
                ("risk_currency", args.adversarial_currency),
            )
            if value is not None
        }
        if adverse_kwargs and not args.adversarial:
            raise ValueError("Adversarial options require --adversarial declaration")
        if args.adversarial and not args.monte_carlo:
            raise ValueError("Version 1.2 adversarial policy requires --monte-carlo")
        adverse_spec = AdversarialSpec(**adverse_kwargs) if args.adversarial else None
        if args.strategy == "moving-average-directional" and mc_spec is not None:
            raise ValueError(
                "Directional research needs a reviewed reversal-fee Monte Carlo/adversarial policy"
            )
        if (
            output
            in {
                args.development.resolve(),
                args.holdout.resolve(),
                root / "research.sqlite3",
                root / "research.sqlite3-wal",
                root / "research.sqlite3-shm",
            }
            or output.is_relative_to(root / "archives")
            or output.is_relative_to(root / "strategies")
        ):
            raise ValueError("Output cannot overwrite input or research storage")
        candidates = []
        for value in args.candidate:
            pair = value.split(":")
            if len(pair) != 2:
                raise ValueError("Candidate requires FAST:SLOW")
            fast, slow = map(int, pair)
            candidates.append(
                StrategyGenome(
                    DIRECTIONAL_REF
                    if args.strategy == "moving-average-directional"
                    else "moving-average-cross@1.0.0",
                    "BAR",
                    (("fast", fast), ("slow", slow)),
                )
            )
        archive = BarArchive(root / "archives")
        sources, datasets = [], []
        for role, path in (("DEVELOPMENT", args.development), ("HOLDOUT", args.holdout)):
            with path.open("rb") as source:
                raw = source.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise ValueError("Research CSV exceeds byte capacity")
            manifest = archive.ingest_csv_bytes(
                raw, source_id="research-" + role.lower(), ingested_at=datetime.now(timezone.utc)
            )
            dataset = archive.replay(manifest.archive_id)
            if not 2 <= len(dataset.bars) <= 4096:
                raise ValueError("Research dataset requires 2..4096 canonical bars")
            datasets.append(dataset)
            sources.append(
                {
                    "role": role,
                    "archive_id": manifest.archive_id,
                    "source_sha256": manifest.source_sha256,
                    "dataset_fingerprint": manifest.dataset_fingerprint,
                }
            )
        development, holdout = datasets

        def scope(dataset):
            return ResearchDatasetScope(
                dataset.bars[0].instrument_id,
                dataset.bars[0].observed_at,
                dataset.bars[-1].observed_at,
                dataset.fingerprint,
            )

        plan = ResearchPlan(
            args.experiment_id,
            Hypothesis(args.hypothesis_id, args.description),
            tuple(candidates),
            development.fingerprint,
            scope(development),
            holdout.fingerprint,
            scope(holdout),
            args.train_bars,
            args.validation_bars,
            args.quantity,
            BacktestCosts(args.slippage_bps, args.fee),
            BacktestCosts(args.stress_slippage_bps, args.stress_fee),
            schema_version="1.2"
            if adverse_spec is not None
            else "1.1"
            if mc_spec is not None
            else "1.0",
            monte_carlo_spec=mc_spec,
            adversarial_spec=adverse_spec,
        )
        report = run_research_experiment(
            plan,
            development,
            holdout,
            StrategyRegistry(root / "strategies"),
            ResearchStore(root / "research.sqlite3"),
        )
        payload = {
            "schema_version": report.schema_version,
            "report": json.loads(encode_research(report)),
            "report_sha256": sha256(encode_research(report)).hexdigest(),
            "source_lineage": sources,
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
        )
        print(report.outcome + "; promotion_allowed=false")
    except ImportError:
        parser.error(
            'Install the research prerequisites: python -m pip install -e ".[data,trading]"'
        )
    except (ValueError, OSError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
