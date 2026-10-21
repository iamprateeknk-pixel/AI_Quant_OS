"""Server-owned catalog, lifecycle and permission evidence for A01 paper orchestration."""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.agent_context import EvidenceRecord, SourceBinding
from ai_quant_os.agent_permissions import READ_TOOLS, SANDBOX_TOOLS
from ai_quant_os.agent_registry import AgentManifest, AgentRegistry, Lifecycle
from ai_quant_os.macro_sources import MacroWorldEvidence
from ai_quant_os.world_state import WorldState

SPECIALISTS = ("A07", "A06", "A05", "A02", "A04", "A03")
SUPERVISOR_INPUT_BYTES = 65536
SUPERVISOR_CONTEXT_BYTES = 160000


class SupervisorObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    as_of: AwareDatetime
    world_state_version: str = Field(pattern=r"^world:[a-f0-9]{64}$")


class CatalogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    manifest: AgentManifest
    lifecycle: Lifecycle
    executable: bool


class RegistryEvidence(SupervisorObservation):
    entries: tuple[CatalogEntry, ...] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def exact_catalog(self):
        keys = [(row.manifest.agent_id, row.manifest.agent_version) for row in self.entries]
        if keys != sorted(set(keys)):
            raise ValueError("Supervisor catalog must have unique ordered versions")
        for row in self.entries:
            expected = (
                row.manifest.agent_id in (*SPECIALISTS, "A01")
                and row.manifest.agent_version == "0.2.0"
            )
            if row.executable != expected or (
                row.executable and row.manifest.implementation_status == "PLANNED"
            ):
                raise ValueError("Unsupported supervisor execution catalog")
        return self


class PermissionEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    manifest_ref: str = Field(pattern=r"^agent-manifest:[a-f0-9]{64}$")
    tools: tuple[str, ...] = Field(max_length=32)


class PermissionsEvidence(SupervisorObservation):
    scope: Literal["PAPER"] = "PAPER"
    entries: tuple[PermissionEntry, ...] = Field(min_length=1, max_length=128)


class HealthEvidence(SupervisorObservation):
    status: Literal["UNAVAILABLE", "RESTORED_ONLY", "MONITORED", "ERROR"]
    open_incidents: int | None = Field(ge=0, le=10000)
    reputation_status: Literal["UNAVAILABLE"] = "UNAVAILABLE"

    @model_validator(mode="after")
    def available_count(self):
        if (self.status == "UNAVAILABLE") != (self.open_incidents is None):
            raise ValueError("Health availability and incident count disagree")
        return self


def effective_tools(entry: CatalogEntry) -> tuple[str, ...]:
    if entry.lifecycle == "QUARANTINED":
        return ()
    allowed = set(entry.manifest.allowed_tools) - SANDBOX_TOOLS
    if entry.lifecycle == "LIMITED":
        allowed &= READ_TOOLS
    return tuple(sorted(allowed))


class SupervisorEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    agent_registry: RegistryEvidence
    world_state: MacroWorldEvidence
    health: HealthEvidence
    permissions: PermissionsEvidence

    @model_validator(mode="after")
    def bound_snapshot(self):
        world = self.world_state.world
        for observation in (self.agent_registry, self.health, self.permissions):
            if (observation.as_of, observation.world_state_version) != (
                world.as_of,
                world.world_state_version,
            ):
                raise ValueError("Supervisor evidence refers to different observations")
        expected = tuple(
            PermissionEntry(manifest_ref=row.manifest.manifest_ref, tools=effective_tools(row))
            for row in self.agent_registry.entries
        )
        if self.permissions.entries != expected:
            raise ValueError("Supervisor permissions do not match catalog lifecycle")
        return self


def supervisor_sources(
    world: WorldState,
    registry: AgentRegistry,
    *,
    health_status="UNAVAILABLE",
    open_incidents=None,
    version="0.2.0",
    task_id=None,
    task_kind=None,
    artifact_ref=None,
    review_ref=None,
) -> dict[str, SourceBinding]:
    common = dict(as_of=world.as_of, world_state_version=world.world_state_version)
    extra = {}
    permissions_type, permission_tools = PermissionsEvidence, effective_tools
    if version not in {"0.4.0", "0.5.0", "0.6.0", "0.7.0", "0.8.0", "0.9.0"} and (
        task_id is not None
        or task_kind is not None
        or artifact_ref is not None
        or review_ref is not None
    ):
        raise ValueError("Task declaration requires a declared-task supervisor version")
    if version == "0.4.0":
        from ai_quant_os.task_supervisor import (
            TaskRegistryEvidence,
            TaskSupervisorEvidence,
            TaskDeclaration,
            executable,
        )

        registry_type, evidence_type = TaskRegistryEvidence, TaskSupervisorEvidence
        extra = {"task_spec": TaskDeclaration(**common, task_id=task_id, task_kind=task_kind)}
    elif version == "0.5.0":
        from ai_quant_os.task_supervisor_incident import (
            TaskRegistryEvidence,
            TaskSupervisorEvidence,
            TaskDeclaration,
            executable,
        )

        registry_type, evidence_type = TaskRegistryEvidence, TaskSupervisorEvidence
        extra = {"task_spec": TaskDeclaration(**common, task_id=task_id, task_kind=task_kind)}
    elif version == "0.6.0":
        from ai_quant_os.task_supervisor_research import (
            SandboxPermissionsEvidence,
            TaskDeclaration,
            TaskRegistryEvidence,
            TaskSupervisorEvidence,
            executable,
            sandbox_effective_tools,
        )

        registry_type, evidence_type = TaskRegistryEvidence, TaskSupervisorEvidence
        permissions_type, permission_tools = SandboxPermissionsEvidence, sandbox_effective_tools
        extra = {"task_spec": TaskDeclaration(**common, task_id=task_id, task_kind=task_kind)}
    elif version == "0.7.0":
        from ai_quant_os.task_supervisor_research import (
            SandboxPermissionsEvidence,
            TaskDeclaration,
            TaskRegistryEvidenceV3 as TaskRegistryEvidence,
            TaskSupervisorEvidenceV3 as TaskSupervisorEvidence,
            executable_v3 as executable,
            sandbox_effective_tools,
        )

        registry_type, evidence_type = TaskRegistryEvidence, TaskSupervisorEvidence
        permissions_type, permission_tools = SandboxPermissionsEvidence, sandbox_effective_tools
        extra = {"task_spec": TaskDeclaration(**common, task_id=task_id, task_kind=task_kind)}
    elif version == "0.8.0":
        from ai_quant_os.task_supervisor_research import (
            SandboxPermissionsEvidence,
            ValidationTaskDeclaration,
            TaskRegistryEvidenceV4 as TaskRegistryEvidence,
            TaskSupervisorEvidenceV4 as TaskSupervisorEvidence,
            executable_v4 as executable,
            sandbox_effective_tools,
        )

        if task_kind != "VALIDATION_REVIEW" or artifact_ref is None:
            raise ValueError("Validation review declaration requires its artifact reference")
        registry_type, evidence_type = TaskRegistryEvidence, TaskSupervisorEvidence
        permissions_type, permission_tools = SandboxPermissionsEvidence, sandbox_effective_tools
        extra = {
            "task_spec": ValidationTaskDeclaration(
                **common,
                task_id=task_id,
                task_kind=task_kind,
                artifact_ref=artifact_ref,
            )
        }
    elif version == "0.9.0":
        from ai_quant_os.task_supervisor_research import (
            ConstructionTaskDeclaration,
            TaskRegistryEvidenceV5 as TaskRegistryEvidence,
            TaskSupervisorEvidenceV5 as TaskSupervisorEvidence,
            executable_v5 as executable,
            SandboxPermissionsEvidence,
            sandbox_effective_tools,
        )

        if task_kind != "STRATEGY_CONSTRUCTION" or review_ref is None:
            raise ValueError("Construction declaration requires its active review reference")
        registry_type, evidence_type = TaskRegistryEvidence, TaskSupervisorEvidence
        permissions_type, permission_tools = SandboxPermissionsEvidence, sandbox_effective_tools
        extra = {
            "task_spec": ConstructionTaskDeclaration(
                **common, task_id=task_id, task_kind=task_kind, review_ref=review_ref
            )
        }
    elif version == "0.3.0":
        from ai_quant_os.supervisor_drift import (
            DriftRegistryEvidence,
            DriftSupervisorEvidence,
            executable,
        )

        registry_type, evidence_type = DriftRegistryEvidence, DriftSupervisorEvidence
    elif version == "0.2.0":
        registry_type, evidence_type = RegistryEvidence, SupervisorEvidence

        def executable(agent, version):
            return agent in (*SPECIALISTS, "A01") and version == "0.2.0"
    else:
        raise ValueError("Unsupported supervisor source version")
    entries = tuple(
        CatalogEntry(
            manifest=row,
            lifecycle=registry.lifecycle(row.agent_id, row.agent_version),
            executable=executable(row.agent_id, row.agent_version),
        )
        for row in registry.manifests()
    )
    evidence = evidence_type(
        **extra,
        agent_registry=registry_type(**common, entries=entries),
        world_state=MacroWorldEvidence(world=world),
        health=HealthEvidence(**common, status=health_status, open_incidents=open_incidents),
        permissions=permissions_type(
            **common,
            entries=tuple(
                PermissionEntry(
                    manifest_ref=row.manifest.manifest_ref,
                    tools=permission_tools(row),
                )
                for row in entries
            ),
        ),
    )
    sources = {}
    for name, tool in (
        ("agent_registry", "HEALTH_READ"),
        ("world_state", "WORLD_STATE_READ"),
        ("health", "HEALTH_READ"),
        ("permissions", "HEALTH_READ"),
        *((("task_spec", "HEALTH_READ"),) if extra else ()),
    ):
        record = EvidenceRecord(
            source_id=f"paper-supervisor:{name}:{world.world_state_version}",
            as_of=world.as_of,
            valid_until=world.valid_until,
            trusted=True,
            payload_json=getattr(evidence, name).model_dump_json(),
        )

        async def fetch(record=record):
            return record

        sources[name] = SourceBinding(tool, "GENERAL", fetch)
    return sources
