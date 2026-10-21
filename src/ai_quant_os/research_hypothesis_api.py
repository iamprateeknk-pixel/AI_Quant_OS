"""Operator-only trusted A08 research graphs; sandbox scope supplies no OS isolation."""

from typing import Awaitable, Callable, Literal
import sqlite3

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ai_quant_os.agent_context import CompiledContext
from ai_quant_os.graph_runtime import GraphRuntime
from ai_quant_os.graph_ledger import GraphReceipt


class ResearchContextInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str = Field(min_length=1, max_length=128)
    agent_version: Literal["0.2.0", "0.3.0"] = "0.2.0"


class ResearchGraphInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    context_ref: str = Field(min_length=1, max_length=128)


def install_hypothesis_routes(
    app: FastAPI,
    require_operator: Callable,
    *,
    compile_context: Callable[[str, str], Awaitable[CompiledContext]],
    archived_context: Callable[[str], CompiledContext],
    runtime: Callable[[], GraphRuntime],
    observe_receipt: Callable[[GraphReceipt], Awaitable[None]],
) -> None:
    @app.post("/api/v1/sandbox/research/context", dependencies=[Depends(require_operator)])
    async def compile_research(payload: ResearchContextInput) -> dict:
        try:
            context = await compile_context(payload.task_id, payload.agent_version)
        except PermissionError as error:
            raise HTTPException(403, "Research context admission denied") from error
        except (ValueError, OSError, sqlite3.Error) as error:
            raise HTTPException(
                503, "Research source or checkpoint integrity unavailable"
            ) from error
        return {
            "mode": "PAPER",
            **context.model_dump(mode="json"),
            "checkpoint_ref": context.context_ref,
        }

    def research_context(context_ref: str) -> CompiledContext:
        context = archived_context(context_ref)
        if (
            context.agent_id != "A08"
            or context.agent_version not in {"0.2.0", "0.3.0"}
            or context.scope != "SANDBOX"
        ):
            raise PermissionError("Research graph requires its A08 sandbox context")
        return context

    @app.post("/api/v1/sandbox/research/execute", dependencies=[Depends(require_operator)])
    async def execute_research(payload: ResearchGraphInput) -> dict:
        engine = runtime()
        try:
            context = research_context(payload.context_ref)
            receipt = await engine.run(payload.run_id, context)
            await observe_receipt(receipt)
        except PermissionError as error:
            raise HTTPException(403, "Research graph admission denied") from error
        except ValueError as error:
            raise HTTPException(409, "Research graph rejected") from error
        except sqlite3.Error as error:
            raise HTTPException(503, "Research graph storage unavailable") from error
        return {
            "mode": "PAPER",
            "scope": "SANDBOX",
            "advisory_only": True,
            "context_current": engine.current(receipt.run_id),
            "receipt": receipt.model_dump(mode="json"),
        }

    @app.post(
        "/api/v1/sandbox/research/graph-runs/{run_id}/resume",
        dependencies=[Depends(require_operator)],
    )
    async def resume_research(run_id: str) -> dict:
        engine = runtime()
        try:
            original = engine.receipt(run_id)
            research_context(original.context_ref)
            receipt = await engine.resume(run_id)
            await observe_receipt(receipt)
        except KeyError as error:
            raise HTTPException(404, "Research graph not found") from error
        except PermissionError as error:
            raise HTTPException(403, "Research graph admission denied") from error
        except (ValueError, sqlite3.Error) as error:
            raise HTTPException(503, "Research graph integrity unavailable") from error
        return {
            "mode": "PAPER",
            "scope": "SANDBOX",
            "advisory_only": True,
            "context_current": engine.current(run_id),
            "receipt": receipt.model_dump(mode="json"),
        }
