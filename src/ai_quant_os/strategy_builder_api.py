"""Operator review and A09 sandbox graph routes; no proposed source execution."""

from datetime import datetime, timedelta
from hashlib import sha256
import sqlite3
from typing import Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ai_quant_os.agent_context import CompiledContext
from ai_quant_os.graph_ledger import GraphRecord, GraphReceipt
from ai_quant_os.graph_runtime import GraphRuntime
from ai_quant_os.hypothesis_review_contracts import HypothesisReview
from ai_quant_os.research_hypothesis_contracts import HypothesisSpec
from ai_quant_os.strategy_build_store import StrategyBuildStore


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    run_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    hypothesis_signature: str = Field(pattern=r"^[a-f0-9]{64}$")
    rationale: str = Field(min_length=1, max_length=2048)
    valid_for_seconds: int = Field(strict=True, ge=1, le=86400, default=3600)


class WithdrawalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=2048)


class BuilderContextInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str = Field(min_length=1, max_length=128)
    review_ref: str = Field(pattern=r"^hypothesis-review:[a-f0-9]{64}$")


class BuilderGraphInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    context_ref: str = Field(min_length=1, max_length=128)


def install_builder_routes(
    app: FastAPI,
    require_operator: Callable,
    *,
    store: StrategyBuildStore,
    graph_record: Callable[[str], GraphRecord],
    clock: Callable[[], datetime],
    compile_context: Callable[[str, str], Awaitable[CompiledContext]],
    archived_context: Callable[[str], CompiledContext],
    runtime: Callable[[], GraphRuntime],
    observe_receipt: Callable[[GraphReceipt], Awaitable[None]],
    require_build_permission: Callable[[], None],
) -> None:

    @app.post("/api/v1/sandbox/research/reviews", dependencies=[Depends(require_operator)])
    def review_hypothesis(payload: ReviewInput) -> dict:
        try:
            record = graph_record(payload.run_id)
            if (record.receipt.agent_id, record.receipt.agent_version, record.receipt.status) != (
                "A08",
                "0.2.0",
                "COMPLETED",
            ):
                raise ValueError("Review requires a completed research graph")
            output = HypothesisSpec.model_validate_json(record.receipt.output_json)
            output.require_context(record.checkpoint.context)
            hypothesis = next(
                (row for row in output.hypotheses if row.signature == payload.hypothesis_signature),
                None,
            )
            if hypothesis is None:
                raise KeyError("Hypothesis not found")
            now = clock()
            if record.receipt.created_at > now:
                raise ValueError("Review cannot accept a future research graph")
            graph_hash = sha256(record.model_dump_json().encode()).hexdigest()
            previous = store.review_by_id(payload.review_id)
            if previous is not None:
                if (
                    previous.run_id,
                    previous.hypothesis,
                    previous.rationale,
                    previous.graph_record_sha256,
                    (previous.valid_until - previous.reviewed_at).total_seconds(),
                ) != (
                    payload.run_id,
                    hypothesis,
                    payload.rationale,
                    graph_hash,
                    payload.valid_for_seconds,
                ):
                    raise ValueError("Immutable review identity conflict")
                review = previous
            else:
                review = store.put_review(
                    HypothesisReview(
                        review_id=payload.review_id,
                        run_id=payload.run_id,
                        graph_record_sha256=graph_hash,
                        hypothesis=hypothesis,
                        rationale=payload.rationale,
                        reviewed_at=now,
                        valid_until=now + timedelta(seconds=payload.valid_for_seconds),
                    )
                )
        except KeyError as error:
            raise HTTPException(404, "Research hypothesis not found") from error
        except PermissionError as error:
            raise HTTPException(403, "Hypothesis review denied") from error
        except ValueError as error:
            raise HTTPException(409, "Hypothesis review rejected") from error
        except sqlite3.Error as error:
            raise HTTPException(503, "Hypothesis review storage unavailable") from error
        return {
            "mode": "PAPER",
            "scope": "SANDBOX",
            "review": {**review.model_dump(mode="json"), "review_ref": review.review_ref},
        }

    @app.get(
        "/api/v1/sandbox/research/reviews/{review_ref}", dependencies=[Depends(require_operator)]
    )
    def get_review(review_ref: str) -> dict:
        try:
            review = store.get_review(review_ref)
            if review is None:
                raise KeyError("Review not found")
            withdrawal = store.withdrawal(review_ref)
            permitted = True
            try:
                require_build_permission()
            except PermissionError:
                permitted = False
        except KeyError as error:
            raise HTTPException(404, "Hypothesis review not found") from error
        except (ValueError, sqlite3.Error) as error:
            raise HTTPException(503, "Hypothesis review integrity unavailable") from error
        return {
            "mode": "PAPER",
            "review": {**review.model_dump(mode="json"), "review_ref": review.review_ref},
            "withdrawal": withdrawal.model_dump(mode="json") if withdrawal else None,
            "build_authorized_current": permitted
            and withdrawal is None
            and review.reviewed_at <= clock() < review.valid_until,
        }

    @app.post(
        "/api/v1/sandbox/research/reviews/{review_ref}/withdraw",
        dependencies=[Depends(require_operator)],
    )
    def withdraw_review(review_ref: str, payload: WithdrawalInput) -> dict:
        try:
            withdrawal = store.withdraw(review_ref, reason=payload.reason, now=clock())
        except KeyError as error:
            raise HTTPException(404, "Hypothesis review not found") from error
        except ValueError as error:
            raise HTTPException(409, "Hypothesis review withdrawal rejected") from error
        except sqlite3.Error as error:
            raise HTTPException(503, "Hypothesis review storage unavailable") from error
        return {
            "mode": "PAPER",
            "withdrawal": withdrawal.model_dump(mode="json"),
            "build_authorized_current": False,
        }

    @app.post("/api/v1/sandbox/strategy-builder/context", dependencies=[Depends(require_operator)])
    async def compile_builder(payload: BuilderContextInput) -> dict:
        try:
            context = await compile_context(payload.task_id, payload.review_ref)
        except KeyError as error:
            raise HTTPException(404, "Hypothesis review not found") from error
        except PermissionError as error:
            raise HTTPException(403, "Candidate build admission denied") from error
        except (ValueError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Candidate context integrity unavailable") from error
        return {
            "mode": "PAPER",
            **context.model_dump(mode="json"),
            "checkpoint_ref": context.context_ref,
        }

    def builder_context(reference: str):
        context = archived_context(reference)
        if (context.agent_id, context.agent_version, context.scope) != ("A09", "0.2.0", "SANDBOX"):
            raise PermissionError("Candidate graph requires its A09 sandbox context")
        return context

    @app.post("/api/v1/sandbox/strategy-builder/execute", dependencies=[Depends(require_operator)])
    async def execute_builder(payload: BuilderGraphInput) -> dict:
        engine = runtime()
        try:
            receipt = await engine.run(payload.run_id, builder_context(payload.context_ref))
            await observe_receipt(receipt)
        except PermissionError as error:
            raise HTTPException(403, "Candidate graph admission denied") from error
        except ValueError as error:
            raise HTTPException(409, "Candidate graph rejected") from error
        except sqlite3.Error as error:
            raise HTTPException(503, "Candidate graph storage unavailable") from error
        return {
            "mode": "PAPER",
            "scope": "SANDBOX",
            "advisory_only": True,
            "context_current": engine.current(receipt.run_id),
            "receipt": receipt.model_dump(mode="json"),
        }

    @app.post(
        "/api/v1/sandbox/strategy-builder/graph-runs/{run_id}/resume",
        dependencies=[Depends(require_operator)],
    )
    async def resume_builder(run_id: str) -> dict:
        engine = runtime()
        try:
            builder_context(engine.receipt(run_id).context_ref)
            receipt = await engine.resume(run_id)
            await observe_receipt(receipt)
        except KeyError as error:
            raise HTTPException(404, "Candidate graph not found") from error
        except PermissionError as error:
            raise HTTPException(403, "Candidate graph admission denied") from error
        except (ValueError, sqlite3.Error) as error:
            raise HTTPException(503, "Candidate graph integrity unavailable") from error
        return {
            "mode": "PAPER",
            "scope": "SANDBOX",
            "advisory_only": True,
            "context_current": engine.current(run_id),
            "receipt": receipt.model_dump(mode="json"),
        }

    @app.get(
        "/api/v1/sandbox/strategy-builder/candidates/{candidate_ref}",
        dependencies=[Depends(require_operator)],
    )
    def get_candidate(candidate_ref: str) -> dict:
        try:
            candidate = store.get_candidate(candidate_ref)
            if candidate is None:
                raise KeyError("Candidate not found")
        except KeyError as error:
            raise HTTPException(404, "Candidate artifact not found") from error
        except (ValueError, sqlite3.Error) as error:
            raise HTTPException(503, "Candidate artifact integrity unavailable") from error
        return {
            "mode": "PAPER",
            "scope": "SANDBOX",
            "candidate": candidate.model_dump(mode="json"),
            "candidate_ref": candidate.candidate_ref,
            "execution_authorized": False,
        }
