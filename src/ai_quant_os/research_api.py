"""Operator-protected local research dossier routes; native dependencies load on demand."""

from pathlib import Path
import sqlite3

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field


class CandidateRegistrationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    experiment_id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")


class CandidateWithdrawalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(strict=True, ge=1, le=100000)
    command_id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
    reason: str = Field(min_length=1, max_length=512)


def install_research_routes(app: FastAPI, workspace: Path | None, require_operator):
    root = workspace.resolve() if workspace is not None else None

    def store():
        if root is None:
            raise HTTPException(status_code=503, detail="Research workspace is disabled")
        try:
            from ai_quant_os.research_candidate_store import CandidateStore

            return CandidateStore(root / "candidates.sqlite3")
        except (ImportError, ValueError, OSError, sqlite3.Error) as error:
            raise HTTPException(
                status_code=503, detail="Research registry is unavailable"
            ) from error

    @app.get("/api/v1/research/candidates", dependencies=[Depends(require_operator)])
    def candidates():
        try:
            return {"schema_version": "1.0", "candidates": store().list()}
        except (ValueError, OSError, sqlite3.Error) as error:
            raise HTTPException(
                status_code=503, detail="Research registry verification failed"
            ) from error

    @app.get(
        "/api/v1/research/candidates/{candidate_id}/bundle",
        dependencies=[Depends(require_operator)],
    )
    def candidate_bundle(candidate_id: str):
        try:
            bundle = store().bundle(candidate_id)
        except (ValueError, OSError, sqlite3.Error) as error:
            raise HTTPException(
                status_code=503, detail="Research bundle verification failed"
            ) from error
        if bundle is None:
            raise HTTPException(status_code=404, detail="Unknown research candidate")
        # The canonical encoder preserves legacy report hashes nested in this new envelope.
        from ai_quant_os.research_store import encode_research
        from fastapi.responses import Response

        return Response(encode_research(bundle), media_type="application/json")

    @app.post("/api/v1/paper/research/candidates", dependencies=[Depends(require_operator)])
    def register_candidate(payload: CandidateRegistrationInput):
        registry = store()
        from ai_quant_os.research_candidates import build_candidate_bundle
        from ai_quant_os.research_store import ResearchStore
        from ai_quant_os.strategy_registry import StrategyRegistry

        try:
            bundle = build_candidate_bundle(
                ResearchStore(root / "research.sqlite3"),
                StrategyRegistry(root / "strategies"),
                payload.experiment_id,
            )
            return registry.register(bundle)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except (OSError, sqlite3.Error) as error:
            raise HTTPException(
                status_code=503, detail="Research archive is unavailable"
            ) from error

    @app.post(
        "/api/v1/paper/research/candidates/{candidate_id}/withdraw",
        dependencies=[Depends(require_operator)],
    )
    def withdraw_candidate(candidate_id: str, payload: CandidateWithdrawalInput):
        try:
            return store().withdraw(candidate_id, **payload.model_dump())
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except (OSError, sqlite3.Error) as error:
            raise HTTPException(
                status_code=503, detail="Research registry is unavailable"
            ) from error
