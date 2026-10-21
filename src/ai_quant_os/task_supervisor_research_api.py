"""Operator routes for A01 research supervision in the SANDBOX scope."""

import sqlite3
from typing import Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ai_quant_os.agent_context import CompiledContext
from ai_quant_os.graph_ledger import GraphReceipt
from ai_quant_os.graph_runtime import GraphRuntime


class ResearchSupervisorContextInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    task_id: str = Field(min_length=1, max_length=128)


class ResearchSupervisorGraphInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    context_ref: str = Field(min_length=1, max_length=128)


def install_research_supervisor_routes(
    app: FastAPI,
    require_operator: Callable,
    *,
    compile_context: Callable[[str], Awaitable[CompiledContext]],
    archived_context: Callable[[str], CompiledContext],
    runtime: Callable[[], GraphRuntime],
    observe_receipt: Callable[[GraphReceipt], Awaitable[None]],
    route_prefix: str = "/api/v1/sandbox/research/supervise",
    agent_version: str = "0.6.0",
    context_input_model: type[BaseModel] = ResearchSupervisorContextInput,
) -> None:
    @app.post(
        f"{route_prefix}/context",
        dependencies=[Depends(require_operator)],
    )
    async def compile_research_supervisor(payload: context_input_model) -> dict:
        try:
            if hasattr(payload, "artifact_ref"):
                context = await compile_context(payload.task_id, payload.artifact_ref)
            elif hasattr(payload, "review_ref"):
                context = await compile_context(payload.task_id, payload.review_ref)
            else:
                context = await compile_context(payload.task_id)
        except KeyError as error:
            raise HTTPException(404, "Referenced research source not found") from error
        except PermissionError as error:
            raise HTTPException(403, "Research supervisor context admission denied") from error
        except (ValueError, OSError, sqlite3.Error) as error:
            raise HTTPException(
                503, "Research supervisor source or checkpoint integrity unavailable"
            ) from error
        return {
            "mode": "PAPER",
            "scope": "SANDBOX",
            **context.model_dump(mode="json"),
            "checkpoint_ref": context.context_ref,
        }

    def research_supervisor_context(context_ref: str) -> CompiledContext:
        context = archived_context(context_ref)
        if (context.agent_id, context.agent_version, context.scope) != (
            "A01",
            agent_version,
            "SANDBOX",
        ):
            raise PermissionError("Research supervisor requires its A01 sandbox context")
        return context

    @app.post(
        f"{route_prefix}/execute",
        dependencies=[Depends(require_operator)],
    )
    async def execute_research_supervisor(payload: ResearchSupervisorGraphInput) -> dict:
        engine = runtime()
        try:
            context = research_supervisor_context(payload.context_ref)
            receipt = await engine.run(payload.run_id, context)
            await observe_receipt(receipt)
        except PermissionError as error:
            raise HTTPException(403, "Research supervisor graph admission denied") from error
        except ValueError as error:
            raise HTTPException(409, "Research supervisor graph rejected") from error
        except sqlite3.Error as error:
            raise HTTPException(503, "Research supervisor graph storage unavailable") from error
        return {
            "mode": "PAPER",
            "scope": "SANDBOX",
            "advisory_only": True,
            "context_current": engine.current(receipt.run_id),
            "receipt": receipt.model_dump(mode="json"),
        }

    @app.post(
        f"{route_prefix}/graph-runs/{{run_id}}/resume",
        dependencies=[Depends(require_operator)],
    )
    async def resume_research_supervisor(run_id: str) -> dict:
        engine = runtime()
        try:
            original = engine.receipt(run_id)
            research_supervisor_context(original.context_ref)
            receipt = await engine.resume(run_id)
            await observe_receipt(receipt)
        except KeyError as error:
            raise HTTPException(404, "Research supervisor graph not found") from error
        except PermissionError as error:
            raise HTTPException(403, "Research supervisor graph admission denied") from error
        except (ValueError, sqlite3.Error) as error:
            raise HTTPException(503, "Research supervisor graph integrity unavailable") from error
        return {
            "mode": "PAPER",
            "scope": "SANDBOX",
            "advisory_only": True,
            "context_current": engine.current(run_id),
            "receipt": receipt.model_dump(mode="json"),
        }
