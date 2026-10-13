"""Operator-protected captures and publication from server-owned paper sources."""

from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
import sqlite3

from fastapi import Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from ai_quant_os.counterfactual_contracts import ReplayVariant
from ai_quant_os.daily_evidence import DailyEvidence, EvidenceCheckpoint, build_daily_report
from ai_quant_os.daily_execution_evidence import build_daily_execution_evidence
from ai_quant_os.daily_execution_aggregation import aggregate_daily_execution
from ai_quant_os.evidence_store import (
    EvidenceConflictError,
    EvidenceIntegrityError,
    EvidenceStore,
    encode_evidence,
)


class CheckpointInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    checkpoint_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


class DailyPublicationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    day: date
    opening_checkpoint_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    closing_checkpoint_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


class DailyShadowComparisonInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shadow_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    sequence: int = Field(strict=True, ge=2, le=1000)


class ShadowCounterfactualInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    replay_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    sequence: int = Field(strict=True, ge=1, le=1000)
    report_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    variants: tuple[ReplayVariant, ...] = Field(min_length=1, max_length=7)


def install_daily_routes(
    app,
    paper_path: Path | None,
    require_operator,
    ledger,
    execution_ledger,
    quotes,
    catalog,
    clock,
    research_workspace: Path | None = None,
    demo_sync_state_dir: Path | None = None,
):
    def store():
        if paper_path is None:
            raise HTTPException(503, "Persistent paper evidence is disabled")
        return EvidenceStore(paper_path.with_name(paper_path.stem + ".evidence.sqlite3"))

    def response(value):
        return Response(
            encode_evidence(value),
            media_type="application/json",
            headers={"Cache-Control": "no-store"},
        )

    if demo_sync_state_dir is not None and not demo_sync_state_dir.is_absolute():
        raise ValueError("Demo comparison requires an absolute server-owned state directory")

    @app.post("/api/v1/paper/evidence/checkpoints", dependencies=[Depends(require_operator)])
    def capture(payload: CheckpointInput):
        if payload.checkpoint_id.startswith("daily-auto-"):
            raise HTTPException(409, "Automatic boundary identifiers are reserved")
        try:
            archive = store()
            existing = archive.get_checkpoint(payload.checkpoint_id)
            if existing is not None:
                return response(existing)
            now = clock()
            owners, fills = ledger()
            checkpoint = EvidenceCheckpoint(
                payload.checkpoint_id, now, owners, fills, quotes(), catalog
            )
            return response(archive.checkpoint(checkpoint))
        except (ValueError, TypeError, KeyError, ArithmeticError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Paper evidence capture could not be verified") from error

    @app.post("/api/v1/paper/evidence/daily", dependencies=[Depends(require_operator)])
    def publish(payload: DailyPublicationInput):
        try:
            archive = store()
            opening = archive.get_checkpoint(payload.opening_checkpoint_id)
            closing = archive.get_checkpoint(payload.closing_checkpoint_id)
            if opening is None or closing is None:
                raise HTTPException(404, "Unknown paper checkpoint")
            report = build_daily_report(payload.day, opening, closing)
            evidence = DailyEvidence(opening, closing, report, clock())
            return response(archive.publish(evidence))
        except ValueError as error:
            raise HTTPException(
                409, "Daily evidence window, lineage or publication conflict"
            ) from error
        except (TypeError, KeyError, ArithmeticError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Daily paper evidence could not be verified") from error

    @app.get("/api/v1/paper/evidence/daily/{day}", dependencies=[Depends(require_operator)])
    def daily(day: date):
        try:
            value = store().daily(day)
            if value is None:
                raise HTTPException(404, "Unknown daily paper evidence")
            return response(value)
        except (ValueError, TypeError, KeyError, ArithmeticError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Daily paper evidence could not be verified") from error

    @app.get(
        "/api/v1/paper/evidence/daily-execution-attribution",
        dependencies=[Depends(require_operator)],
    )
    def daily_execution_aggregate(start: date, end: date):
        try:
            archive = store()
            evidence = archive.daily_execution_range(start, end)
            report = aggregate_daily_execution(start, end, evidence)
            return response(report)
        except EvidenceIntegrityError as error:
            raise HTTPException(503, "Daily execution attribution could not be verified") from error
        except ValueError as error:
            raise HTTPException(422, "Daily execution attribution range is invalid") from error
        except (TypeError, KeyError, ArithmeticError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Daily execution attribution could not be verified") from error

    @app.post(
        "/api/v1/paper/evidence/daily/{day}/execution-attribution",
        dependencies=[Depends(require_operator)],
    )
    def publish_execution_attribution(day: date):
        try:
            archive = store()
            prior = archive.daily_execution(day)
            daily_value = archive.daily(day)
        except (ValueError, TypeError, KeyError, ArithmeticError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Daily execution evidence could not be verified") from error
        if prior is not None:
            return response(prior)
        if daily_value is None:
            raise HTTPException(404, "Unknown daily paper evidence")
        try:
            owners, fills, benchmarks = execution_ledger()
        except (ValueError, TypeError, KeyError, ArithmeticError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Paper execution ledger could not be verified") from error
        try:
            evidence = build_daily_execution_evidence(
                daily_value,
                owners,
                fills,
                benchmarks,
                published_at=clock(),
            )
        except ValueError as error:
            raise HTTPException(
                409, "Daily execution attribution conflicts with retained evidence"
            ) from error
        try:
            value = archive.publish_daily_execution(evidence)
        except EvidenceIntegrityError as error:
            raise HTTPException(503, "Daily execution evidence could not be verified") from error
        except (EvidenceConflictError, ValueError) as error:
            raise HTTPException(
                409, "Daily execution attribution conflicts with retained evidence"
            ) from error
        except (TypeError, KeyError, ArithmeticError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Daily execution attribution could not be verified") from error
        return response(value)

    @app.get(
        "/api/v1/paper/evidence/daily/{day}/execution-attribution",
        dependencies=[Depends(require_operator)],
    )
    def daily_execution_attribution(day: date):
        try:
            value = store().daily_execution(day)
            if value is None:
                raise HTTPException(404, "Unknown daily execution attribution")
            return response(value)
        except (ValueError, TypeError, KeyError, ArithmeticError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Daily execution attribution could not be verified") from error

    @app.get(
        "/api/v1/paper/evidence/daily-shadow-comparison",
        dependencies=[Depends(require_operator)],
    )
    def daily_shadow_comparison_aggregate(start: date, end: date, shadow_id: str):
        try:
            daily_evidence = store().daily_execution_range(start, end)
        except EvidenceIntegrityError as error:
            raise HTTPException(503, "Daily PAPER/shadow sources could not be verified") from error
        except ValueError as error:
            raise HTTPException(422, "Daily PAPER/shadow range is invalid") from error
        except (TypeError, KeyError, ArithmeticError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Daily PAPER/shadow sources could not be verified") from error
        if research_workspace is None:
            raise HTTPException(503, "Shadow evidence workspace is disabled")
        shadow_path = research_workspace / "shadow.sqlite3"
        if not shadow_path.is_file():
            raise HTTPException(404, "Unknown shadow portfolio")
        try:
            from ai_quant_os.shadow_store import ShadowStore

            shadow_store = ShadowStore(shadow_path)
            if shadow_store.lookup(shadow_id) is None:
                raise HTTPException(404, "Unknown shadow portfolio")
            history = shadow_store.history(shadow_id)
        except HTTPException:
            raise
        except (ValueError, TypeError, KeyError, ArithmeticError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Shadow evidence could not be verified") from error
        try:
            from ai_quant_os.paper_shadow_comparison import aggregate_daily_paper_shadow

            report = aggregate_daily_paper_shadow(start, end, shadow_id, daily_evidence, history)
            return response(report)
        except ValueError as error:
            raise HTTPException(409, "Daily PAPER/shadow evidence does not reconcile") from error
        except ArithmeticError as error:
            raise HTTPException(
                409, "Daily PAPER/shadow amounts cannot be aggregated exactly"
            ) from error

    @app.post(
        "/api/v1/paper/evidence/daily/{day}/shadow-comparison",
        dependencies=[Depends(require_operator)],
    )
    def daily_shadow_comparison(day: date, payload: DailyShadowComparisonInput):
        try:
            daily_execution = store().daily_execution(day)
        except EvidenceIntegrityError as error:
            raise HTTPException(503, "Daily execution evidence could not be verified") from error
        except (ValueError, TypeError, KeyError, ArithmeticError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Daily execution evidence could not be verified") from error
        if daily_execution is None:
            raise HTTPException(404, "Unknown daily execution attribution")
        if research_workspace is None:
            raise HTTPException(503, "Shadow evidence workspace is disabled")
        shadow_path = research_workspace / "shadow.sqlite3"
        if not shadow_path.is_file():
            raise HTTPException(404, "Unknown shadow portfolio")
        try:
            from ai_quant_os.shadow_store import ShadowStore

            shadow_store = ShadowStore(shadow_path)
            if shadow_store.lookup(payload.shadow_id) is None:
                raise HTTPException(404, "Unknown shadow portfolio")
            history = shadow_store.history(payload.shadow_id)
        except HTTPException:
            raise
        except (ValueError, TypeError, KeyError, ArithmeticError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Shadow evidence could not be verified") from error
        if payload.sequence > len(history):
            raise HTTPException(404, "Unknown shadow observation sequence")
        start_index = payload.sequence - 1
        day_start = datetime.combine(day, time.min, tzinfo=timezone.utc)
        day_end = day_start + timedelta(days=1)
        while start_index > 1:
            previous_batch = history[start_index - 1]
            if not previous_batch.request.bars or any(
                not day_start <= bar.observed_at < day_end for bar in previous_batch.request.bars
            ):
                break
            start_index -= 1
        prior = history[start_index - 1]
        batches = history[start_index : payload.sequence]
        try:
            from ai_quant_os.paper_shadow_comparison import (
                compare_daily_paper_to_shadow_batches,
            )

            return response(compare_daily_paper_to_shadow_batches(daily_execution, prior, batches))
        except ValueError as error:
            raise HTTPException(
                409, "PAPER/shadow evidence does not link to this UTC day"
            ) from error
        except ArithmeticError as error:
            raise HTTPException(409, "PAPER/shadow amounts cannot be compared exactly") from error

    @app.get(
        "/api/v1/paper/shadows/{shadow_id}/reality-gap-alerts",
        dependencies=[Depends(require_operator)],
    )
    def shadow_reality_gap_alerts(shadow_id: str):
        if research_workspace is None:
            raise HTTPException(503, "Shadow evidence workspace is disabled")
        shadow_path = research_workspace / "shadow.sqlite3"
        if not shadow_path.is_file():
            raise HTTPException(404, "Unknown shadow portfolio")
        try:
            from ai_quant_os.shadow_reality_gap import assess_shadow_reality_gap
            from ai_quant_os.shadow_store import ShadowStore

            shadow_store = ShadowStore(shadow_path)
            if shadow_store.lookup(shadow_id) is None:
                raise HTTPException(404, "Unknown shadow portfolio")
            policy = shadow_store.reality_gap_policy(shadow_id)
            alerts = []
            if policy is not None:
                for report in shadow_store.history(shadow_id):
                    assessment = assess_shadow_reality_gap(report, policy)
                    if assessment.state == "DEMOTION_REQUIRED":
                        alerts.append(
                            {
                                "candidate_id": assessment.candidate_id,
                                "shadow_id": assessment.shadow_id,
                                "sequence": report.sequence,
                                "report_sha256": assessment.report_sha256,
                                "policy_ref": policy.content_ref,
                                "gap_bps": str(assessment.gap_bps),
                                "threshold_bps": str(policy.adverse_gap_threshold_bps),
                                "state": assessment.state,
                            }
                        )
            return Response(
                json.dumps({"alerts": alerts}, sort_keys=True, separators=(",", ":")),
                media_type="application/json",
                headers={"Cache-Control": "no-store"},
            )
        except HTTPException:
            raise
        except (ValueError, TypeError, KeyError, ArithmeticError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Shadow alert evidence could not be verified") from error

    @app.get(
        "/api/v1/paper/shadows/{shadow_id}/demo-position-comparison",
        dependencies=[Depends(require_operator)],
    )
    def demo_shadow_position_comparison(shadow_id: str, sequence: int = Query(ge=1, le=1000)):
        if research_workspace is None or demo_sync_state_dir is None:
            raise HTTPException(503, "Demo or shadow evidence workspace is disabled")
        shadow_path = research_workspace / "shadow.sqlite3"
        if not shadow_path.is_file():
            raise HTTPException(404, "Unknown shadow portfolio")
        try:
            from ai_quant_os.shadow_store import ShadowStore

            shadow_store = ShadowStore(shadow_path)
            if shadow_store.lookup(shadow_id) is None:
                raise HTTPException(404, "Unknown shadow portfolio")
            history = shadow_store.history(shadow_id)
        except HTTPException:
            raise
        except (ValueError, TypeError, KeyError, ArithmeticError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Shadow evidence could not be verified") from error
        if sequence > len(history):
            raise HTTPException(404, "Unknown shadow observation sequence")
        report = history[sequence - 1]
        try:
            from ai_quant_os.mt5_portfolio_observation import read_portfolio
            from ai_quant_os.demo_shadow_position_comparison import (
                compare_demo_shadow_position,
            )

            demo = read_portfolio(demo_sync_state_dir / "portfolio.json", clock=clock)
            if (
                demo.state != "AVAILABLE"
                or demo.publication is None
                or demo.publication.record is None
            ):
                raise HTTPException(503, "A fresh demo portfolio observation is unavailable")
            return response(compare_demo_shadow_position(demo.publication.record, report))
        except HTTPException:
            raise
        except ValueError as error:
            raise HTTPException(409, "Demo/shadow position evidence cannot be compared") from error
        except (TypeError, KeyError, ArithmeticError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Demo/shadow evidence could not be verified") from error

    @app.get(
        "/api/v1/paper/shadows/{shadow_id}/demo-trajectory-comparison",
        dependencies=[Depends(require_operator)],
    )
    def demo_shadow_trajectory_comparison(shadow_id: str, sequence: int = Query(ge=1, le=1000)):
        if research_workspace is None or demo_sync_state_dir is None:
            raise HTTPException(503, "Demo or shadow evidence workspace is disabled")
        shadow_path = research_workspace / "shadow.sqlite3"
        projection_path = demo_sync_state_dir / "projection.sqlite"
        if not shadow_path.is_file():
            raise HTTPException(404, "Unknown shadow portfolio")
        if not projection_path.is_file():
            raise HTTPException(503, "Retained demo position history is unavailable")
        try:
            from ai_quant_os.shadow_store import ShadowStore

            shadow_store = ShadowStore(shadow_path)
            if shadow_store.lookup(shadow_id) is None:
                raise HTTPException(404, "Unknown shadow portfolio")
            history = shadow_store.history(shadow_id)
        except HTTPException:
            raise
        except (ValueError, TypeError, KeyError, ArithmeticError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Shadow evidence could not be verified") from error
        if sequence > len(history):
            raise HTTPException(404, "Unknown shadow observation sequence")

        projection = None
        try:
            from ai_quant_os.demo_shadow_position_comparison import (
                DemoPositionSnapshot,
                compare_demo_shadow_position_history,
            )
            from ai_quant_os.mt5_portfolio_observation import read_portfolio
            from ai_quant_os.mt5_sync_projection import ProjectionRejected, SyncProjectionStore

            demo = read_portfolio(demo_sync_state_dir / "portfolio.json", clock=clock)
            if (
                demo.state != "AVAILABLE"
                or demo.publication is None
                or demo.publication.record is None
            ):
                raise HTTPException(503, "A fresh demo portfolio observation is unavailable")
            projection = SyncProjectionStore(projection_path, clock=clock)
            records = projection.position_observations_for_scope(
                demo.publication.record.account_scope_ref
            )
            if not records or records[-1].receipt_ref != demo.publication.record.source_receipt_ref:
                raise HTTPException(409, "Demo portfolio and retained history have changed")
            latest_demo = read_portfolio(demo_sync_state_dir / "portfolio.json", clock=clock)
            if (
                latest_demo.state != "AVAILABLE"
                or latest_demo.publication is None
                or latest_demo.publication.record is None
                or latest_demo.publication.record.source_receipt_ref
                != demo.publication.record.source_receipt_ref
            ):
                raise HTTPException(409, "Demo portfolio changed during comparison")
            snapshots = tuple(
                DemoPositionSnapshot(
                    account_scope_ref=record.batch.account_scope_ref,
                    source_receipt_ref=record.receipt_ref,
                    observed_at=record.batch.observed_at,
                    history_start=record.batch.history_start,
                    history_through=record.batch.history_end,
                    positions=record.batch.positions,
                )
                for record in records
            )
            report = compare_demo_shadow_position_history(snapshots, history[sequence - 1])
            return response(report)
        except HTTPException:
            raise
        except ProjectionRejected as error:
            raise HTTPException(
                503, "Retained demo position history could not be verified"
            ) from error
        except ValueError as error:
            raise HTTPException(409, "Demo/shadow history cannot be compared") from error
        except (TypeError, KeyError, ArithmeticError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Demo/shadow evidence could not be verified") from error
        finally:
            if projection is not None:
                projection.close()

    @app.post(
        "/api/v1/paper/shadows/{shadow_id}/counterfactual",
        dependencies=[Depends(require_operator)],
    )
    def shadow_counterfactual(shadow_id: str, payload: ShadowCounterfactualInput):
        if research_workspace is None:
            raise HTTPException(503, "Shadow evidence workspace is disabled")
        shadow_path = research_workspace / "shadow.sqlite3"
        if not shadow_path.is_file():
            raise HTTPException(404, "Unknown shadow portfolio")
        try:
            from ai_quant_os.shadow_store import ShadowStore

            shadow_store = ShadowStore(shadow_path)
            assignment = shadow_store.lookup(shadow_id)
            if assignment is None:
                raise HTTPException(404, "Unknown shadow portfolio")
            history = shadow_store.history(shadow_id)
        except HTTPException:
            raise
        except (ValueError, TypeError, KeyError, ArithmeticError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Shadow source history could not be verified") from error
        if payload.sequence > len(history):
            raise HTTPException(404, "Unknown shadow observation sequence")
        source_report = history[payload.sequence - 1]
        try:
            from ai_quant_os.shadow_counterfactual import (
                ShadowCounterfactualInputs,
                ShadowCounterfactualConflictError,
                ShadowCounterfactualIntegrityError,
                ShadowCounterfactualPlan,
                ShadowCounterfactualStore,
                run_shadow_counterfactual,
            )
            from ai_quant_os.strategy_registry import StrategyRegistry

            plan = ShadowCounterfactualPlan(
                payload.replay_id,
                shadow_id,
                payload.sequence,
                payload.report_sha256,
                payload.variants,
            )
            inputs = ShadowCounterfactualInputs(plan, source_report)
        except ValueError as error:
            raise HTTPException(
                409, "Counterfactual declaration does not match shadow evidence"
            ) from error
        try:
            counterfactual_store = ShadowCounterfactualStore(
                research_workspace / "shadow-counterfactual.sqlite3"
            )
            return response(
                run_shadow_counterfactual(
                    inputs,
                    StrategyRegistry(research_workspace / "strategies"),
                    counterfactual_store,
                )
            )
        except HTTPException:
            raise
        except ShadowCounterfactualIntegrityError as error:
            raise HTTPException(
                503, "Retained counterfactual evidence could not be verified"
            ) from error
        except ShadowCounterfactualConflictError as error:
            raise HTTPException(
                409, "Counterfactual replay conflicts with retained evidence"
            ) from error
        except (ValueError, TypeError, KeyError, ArithmeticError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Counterfactual replay could not be verified") from error
