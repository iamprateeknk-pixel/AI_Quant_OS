"""Archival context and the exact immutable manifest used during compilation."""

from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from ai_quant_os.agent_context import CompiledContext, verify_context
from ai_quant_os.agent_permissions import SANDBOX_TOOLS
from ai_quant_os.agent_registry import AgentManifest


class ContextCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"]
    manifest: AgentManifest
    context: CompiledContext

    @model_validator(mode="after")
    def manifest_binding(self) -> "ContextCheckpoint":
        context, manifest = self.context, self.manifest
        verify_context(context)
        if (
            context.agent_id != manifest.agent_id
            or context.agent_version != manifest.agent_version
            or context.manifest_ref != manifest.manifest_ref
            or tuple(row.input_name for row in context.inputs) != manifest.required_inputs
        ):
            raise ValueError("Context checkpoint manifest mismatch")
        for row in context.inputs:
            if row.status != "AVAILABLE":
                continue
            if row.source_tool not in manifest.allowed_tools or (
                row.source_tool in SANDBOX_TOOLS and context.scope != "SANDBOX"
            ):
                raise ValueError("Context checkpoint source permission mismatch")
            if row.resource_class in {"TRAINING", "HOLDOUT"} and (
                context.scope != "SANDBOX"
                or row.resource_class + "_READ" not in manifest.allowed_tools
            ):
                raise ValueError("Context checkpoint dataset permission mismatch")
        return self

    def audit_event(self) -> dict[str, str]:
        context = self.context
        return {
            "action": "PAPER_AGENT_CONTEXT",
            "intent_id": "",
            "reason": context.status,
            "context_ref": context.context_ref,
            "manifest_ref": context.manifest_ref,
            "agent_id": context.agent_id,
            "agent_version": context.agent_version,
            "task_id": context.task_id,
            "expires_at": context.valid_until.isoformat(),
            "checkpoint_hash": sha256(self.model_dump_json().encode()).hexdigest(),
        }
