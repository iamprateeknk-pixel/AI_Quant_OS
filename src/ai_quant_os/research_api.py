"""Operator-protected local research dossier routes; native dependencies load on demand."""

from pathlib import Path
import sqlite3
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from ai_quant_os.research_dossier_views import ResearchDossierPage, dossier_preview
from ai_quant_os.strategy_archive_views import StrategyArchivePage, strategy_preview


class CandidateRegistrationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    experiment_id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")


class CandidateWithdrawalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(strict=True, ge=1, le=100000)
    command_id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
    reason: str = Field(min_length=1, max_length=212)


def install_research_routes(
    app: FastAPI, workspace: Path | None, require_operator, admission_guard=None
):
    root = workspace.resolve() if workspace is not None else None

    @app.get("/api/v1/research/strategies/page", dependencies=[Depends(require_operator)])
    def strategy_page(
        limit: Annotated[int, Query(ge=1, le=10)] = 10,
        offset: Annotated[int, Query(ge=0, le=1000)] = 0,
        head: Annotated[str | None, Query(pattern=r"^[a-f0-9]{64}$")] = None,
    ) -> StrategyArchivePage:
        if offset and head is None:
            raise HTTPException(422, "Continued page requires the original catalog head")
        if root is None or not (root / "strategies").is_dir():
            raise HTTPException(503, "Strategy archive is unavailable")
        try:
            from ai_quant_os.strategy_registry import StrategyCatalogChanged, StrategyRegistry

            digest, rows = StrategyRegistry(root / "strategies").catalog()
            if head is not None and head != digest:
                raise HTTPException(409, "Strategy catalog changed; refresh the first page")
            return StrategyArchivePage(
                head_hash=digest,
                total=len(rows),
                offset=offset,
                limit=limit,
                next_offset=offset + limit if offset + limit < len(rows) else None,
                strategies=tuple(strategy_preview(row) for row in rows[offset : offset + limit]),
            )
        except ImportError as error:
            raise HTTPException(503, "Strategy archive dependencies are unavailable") from error
        except StrategyCatalogChanged as error:
            raise HTTPException(409, "Strategy catalog changed; refresh the first page") from error
        except (ValueError, OSError) as error:
            raise HTTPException(503, "Strategy archive verification failed") from error

    def store(*, create: bool = False):
        if root is None:
            raise HTTPException(status_code=503, detail="Research workspace is disabled")
        try:
            if not root.is_dir():
                raise OSError("Research workspace directory is unavailable")
            if not create and not (root / "candidates.sqlite3").is_file():
                raise OSError("Research registry file is unavailable")
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

    @app.get("/api/v1/research/candidates/page", dependencies=[Depends(require_operator)])
    def dossier_page(
        limit: Annotated[int, Query(ge=1, le=2)] = 2,
        offset: Annotated[int, Query(ge=0, le=1000)] = 0,
        head: Annotated[str | None, Query(pattern=r"^[a-f0-9]{64}$")] = None,
    ) -> ResearchDossierPage:
        if offset and head is None:
            raise HTTPException(422, "Continued page requires the original journal head")
        registry = store()
        from ai_quant_os.research_candidate_store import DossierPageChanged

        try:
            digest, total, dossiers = registry.dossier_page(limit=limit, offset=offset, head=head)
            return ResearchDossierPage(
                head_hash=digest,
                total=total,
                offset=offset,
                limit=limit,
                next_offset=offset + limit if offset + limit < total else None,
                candidates=tuple(dossier_preview(view, bundle) for view, bundle in dossiers),
            )
        except DossierPageChanged as error:
            raise HTTPException(409, "Candidate journal changed; refresh the first page") from error
        except (ValueError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Research dossier verification failed") from error

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
        registry = store(create=True)
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
        dependencies=[Depends(require_operator)]
        + ([Depends(admission_guard)] if admission_guard is not None else []),
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
