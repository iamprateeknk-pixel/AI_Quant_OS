"""Protected SANDBOX graph routes for archived A11 stress verification."""

import sqlite3

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field


class AdversarialContextInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    experiment_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


class AdversarialGraphInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    context_ref: str = Field(pattern=r"^agent-context:[a-f0-9]{64}$")


def install_adversarial_routes(
    app, require_operator, *, compile_context, archived_context, runtime, observe_receipt
):
    def checked_context(reference):
        context = archived_context(reference)
        if (context.agent_id, context.agent_version, context.scope) != (
            "A11",
            "0.2.0",
            "SANDBOX",
        ):
            raise PermissionError("Adversarial graph requires its A11 sandbox context")
        return context

    @app.post(
        "/api/v1/sandbox/adversarial-validator/context", dependencies=[Depends(require_operator)]
    )
    async def compile_adversarial(payload: AdversarialContextInput):
        try:
            context = await compile_context(payload.task_id, payload.experiment_id)
        except KeyError as error:
            raise HTTPException(404, "Completed research experiment not found") from error
        except PermissionError as error:
            raise HTTPException(403, "Adversarial context admission denied") from error
        except (ValueError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Adversarial evidence integrity unavailable") from error
        return {
            "mode": "PAPER",
            **context.model_dump(mode="json"),
            "checkpoint_ref": context.context_ref,
        }

    @app.post(
        "/api/v1/sandbox/adversarial-validator/execute", dependencies=[Depends(require_operator)]
    )
    async def execute_adversarial(payload: AdversarialGraphInput):
        engine = runtime()
        try:
            receipt = await engine.run(payload.run_id, checked_context(payload.context_ref))
            await observe_receipt(receipt)
        except PermissionError as error:
            raise HTTPException(403, "Adversarial graph admission denied") from error
        except ValueError as error:
            raise HTTPException(409, "Adversarial graph rejected") from error
        except sqlite3.Error as error:
            raise HTTPException(503, "Adversarial graph storage unavailable") from error
        return {
            "mode": "PAPER",
            "scope": "SANDBOX",
            "advisory_only": True,
            "context_current": engine.current(receipt.run_id),
            "receipt": receipt.model_dump(mode="json"),
        }

    @app.post(
        "/api/v1/sandbox/adversarial-validator/graph-runs/{run_id}/resume",
        dependencies=[Depends(require_operator)],
    )
    async def resume_adversarial(run_id: str):
        engine = runtime()
        try:
            checked_context(engine.receipt(run_id).context_ref)
            receipt = await engine.resume(run_id)
            await observe_receipt(receipt)
        except KeyError as error:
            raise HTTPException(404, "Adversarial graph not found") from error
        except PermissionError as error:
            raise HTTPException(403, "Adversarial graph admission denied") from error
        except (ValueError, sqlite3.Error) as error:
            raise HTTPException(503, "Adversarial graph integrity unavailable") from error
        return {
            "mode": "PAPER",
            "scope": "SANDBOX",
            "advisory_only": True,
            "context_current": engine.current(run_id),
            "receipt": receipt.model_dump(mode="json"),
        }
