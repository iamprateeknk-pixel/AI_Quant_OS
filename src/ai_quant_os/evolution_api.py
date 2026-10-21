"""Operator-protected genome archive observations; no mutation or execution route."""

import sqlite3
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query

from ai_quant_os.evolution_views import EvolutionGenomePage, genome_preview


def install_evolution_routes(
    app: FastAPI, workspace: Path | None, require_operator, scheduler=None
):
    root = workspace.resolve() if workspace is not None else None

    @app.get("/api/v1/evolution/scheduler", dependencies=[Depends(require_operator)])
    def scheduler_status():
        from fastapi.responses import JSONResponse

        value = (
            {"schema_version": "1.0", "enabled": False, "state": "DISABLED"}
            if scheduler is None
            else scheduler.status()
        )
        return JSONResponse(value, headers={"Cache-Control": "no-store"})

    @app.get("/api/v1/evolution/genomes/page", dependencies=[Depends(require_operator)])
    def genome_page(
        limit: Annotated[int, Query(ge=1, le=10)] = 10,
        offset: Annotated[int, Query(ge=0, le=1000)] = 0,
        head: Annotated[str | None, Query(pattern=r"^[a-f0-9]{64}$")] = None,
    ) -> EvolutionGenomePage:
        if offset and head is None:
            raise HTTPException(422, "Continued page requires the original journal head")
        if root is None or not (root / "genomes.sqlite3").is_file():
            raise HTTPException(503, "Evolution archive is unavailable")
        from ai_quant_os.agent_registry import load_agent_catalog
        from ai_quant_os.genome_store import GenomePageChanged, GenomeStore

        try:
            registry = GenomeStore(root / "genomes.sqlite3", registry=load_agent_catalog())
            digest, total, genomes = registry.page(limit=limit, offset=offset, head=head)
            return EvolutionGenomePage(
                head_hash=digest,
                total=total,
                offset=offset,
                limit=limit,
                next_offset=offset + limit if offset + limit < total else None,
                genomes=tuple(genome_preview(row) for row in genomes),
            )
        except GenomePageChanged as error:
            raise HTTPException(409, "Genome journal changed; refresh the first page") from error
        except (ValueError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Evolution archive verification failed") from error
