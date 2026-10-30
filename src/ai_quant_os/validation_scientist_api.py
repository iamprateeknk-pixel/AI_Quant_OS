"""Protected sandbox context, graph and historical validation-result views."""

import sqlite3
from uuid import uuid4

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ai_quant_os.holdout_validator_rpc import (
    HoldoutContextSnapshot,
    HoldoutRequest,
    HoldoutResponse,
    HoldoutRpcError,
    verify_response,
)
from ai_quant_os.validation_scientist_sources import ValidationContextSnapshot


class ValidationContextInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    artifact_ref: str = Field(pattern=r"^candidate-(validation|prerequisite-failure):[a-f0-9]{64}$")


class ValidationGraphInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    context_ref: str = Field(pattern=r"^agent-context:[a-f0-9]{64}$")


def install_validation_scientist_routes(
    app,
    require_operator,
    *,
    validator_client,
    compile_context,
    archived_context,
    runtime,
    observe_receipt,
):
    def checked_context(reference):
        context = archived_context(reference)
        if (context.agent_id, context.agent_version, context.scope) != ("A10", "0.2.0", "SANDBOX"):
            raise PermissionError("Validation graph requires its A10 sandbox context")
        return context

    @app.post(
        "/api/v1/sandbox/validation-scientist/context", dependencies=[Depends(require_operator)]
    )
    async def compile_validation(payload: ValidationContextInput):
        try:
            context = await compile_context(payload.task_id, payload.artifact_ref)
        except KeyError as error:
            raise HTTPException(404, "Validation result or original package not found") from error
        except PermissionError as error:
            raise HTTPException(403, "Validation context admission denied") from error
        except (ValueError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Validation context integrity unavailable") from error
        return {
            "mode": "PAPER",
            **context.model_dump(mode="json"),
            "checkpoint_ref": context.context_ref,
        }

    @app.post(
        "/api/v1/sandbox/validation-scientist/execute", dependencies=[Depends(require_operator)]
    )
    async def execute_validation(payload: ValidationGraphInput):
        engine = runtime()
        try:
            receipt = await engine.run(payload.run_id, checked_context(payload.context_ref))
            await observe_receipt(receipt)
        except PermissionError as error:
            raise HTTPException(403, "Validation graph admission denied") from error
        except ValueError as error:
            raise HTTPException(409, "Validation graph rejected") from error
        except sqlite3.Error as error:
            raise HTTPException(503, "Validation graph storage unavailable") from error
        return {
            "mode": "PAPER",
            "scope": "SANDBOX",
            "advisory_only": True,
            "context_current": engine.current(receipt.run_id),
            "receipt": receipt.model_dump(mode="json"),
        }

    @app.post(
        "/api/v1/sandbox/validation-scientist/graph-runs/{run_id}/resume",
        dependencies=[Depends(require_operator)],
    )
    async def resume_validation(run_id: str):
        engine = runtime()
        try:
            checked_context(engine.receipt(run_id).context_ref)
            receipt = await engine.resume(run_id)
            await observe_receipt(receipt)
        except KeyError as error:
            raise HTTPException(404, "Validation graph not found") from error
        except PermissionError as error:
            raise HTTPException(403, "Validation graph admission denied") from error
        except (ValueError, sqlite3.Error) as error:
            raise HTTPException(503, "Validation graph integrity unavailable") from error
        return {
            "mode": "PAPER",
            "scope": "SANDBOX",
            "advisory_only": True,
            "context_current": engine.current(run_id),
            "receipt": receipt.model_dump(mode="json"),
        }

    @app.get(
        "/api/v1/sandbox/validation-scientist/results/{artifact_ref}",
        dependencies=[Depends(require_operator)],
    )
    def retained_validation(artifact_ref: str):
        try:
            request = HoldoutRequest(
                request_id=uuid4().hex,
                operation="VALIDATION_CONTEXT",
                agent_id="A10",
                agent_version="0.2.0",
                task_id="validation-result-read",
                context_ref="result-read:" + uuid4().hex,
                evidence_ref=artifact_ref,
            )
            response = validator_client.call(request)
            if type(response) is not HoldoutResponse:
                raise ValueError("Validation result service response is invalid")
            verify_response(request, response)
            envelope = response.context_snapshot
            if type(envelope) is not HoldoutContextSnapshot or envelope.agent_id != "A10":
                raise ValueError("Validation result service omitted its checked summary")
            snapshot = ValidationContextSnapshot.model_validate_json(
                envelope.payload_json, strict=True
            )
            summary = snapshot.summary
            if (
                summary.artifact_ref != artifact_ref
                or response.summary_sha256 != summary.summary_sha256
                or response.aggregate.outcome != summary.outcome
                or response.aggregate.gate_count != len(summary.gates)
            ):
                raise ValueError("Validation result service summary binding mismatch")
        except HoldoutRpcError as error:
            if error.error_code == "REQUEST_REFUSED":
                raise HTTPException(404, "Validation result not found") from error
            raise HTTPException(503, "Validation result service unavailable") from error
        except KeyError as error:
            raise HTTPException(404, "Validation result not found") from error
        except (OSError, ValueError, sqlite3.Error, ConnectionError, TimeoutError) as error:
            raise HTTPException(503, "Validation result integrity unavailable") from error
        return {
            "mode": "PAPER",
            "scope": "SANDBOX",
            "archival_only": True,
            "artifact_ref": artifact_ref,
            "summary": summary.model_dump(mode="json"),
            "promotion_allowed": False,
        }
