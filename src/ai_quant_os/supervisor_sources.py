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
    world: WorldState, registry: AgentRegistry, *, health_status="UNAVAILABLE", open_incidents=None
) -> dict[str, SourceBinding]:
    common = dict(as_of=world.as_of, world_state_version=world.world_state_version)
    entries = tuple(
        CatalogEntry(
            manifest=row,
            lifecycle=registry.lifecycle(row.agent_id, row.agent_version),
            executable=row.agent_id in (*SPECIALISTS, "A01") and row.agent_version == "0.2.0",
        )
        for row in registry.manifests()
    )
    evidence = SupervisorEvidence(
        agent_registry=RegistryEvidence(**common, entries=entries),
        world_state=MacroWorldEvidence(world=world),
        health=HealthEvidence(**common, status=health_status, open_incidents=open_incidents),
        permissions=PermissionsEvidence(
            **common,
            entries=tuple(
                PermissionEntry(manifest_ref=row.manifest.manifest_ref, tools=effective_tools(row))
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
