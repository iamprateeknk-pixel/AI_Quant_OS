# 0015: Versioned agent manifests and fixed tool ceilings

## Decision

Create initial manifests for the exact A01-A18 agent catalog in the execution pack. Each has schema/version, name, squad, mission, required input names, output type, allowed tools, timeout and a canonical content reference. Every initial implementation status is PLANNED and lifecycle SHADOW. These are runtime contracts, not claims that the 18 agent implementations or graphs exist.

Registry registration is immutable by agent ID/version. Exact repeated registration is idempotent; changed reuse conflicts. New versions retain old definitions. Initial version is 0.1.0. There is no implicit latest-version resolution or automatic production promotion. The registry is bounded to 1,024 versions and stored in memory; historical version persistence and lifecycle checkpoints remain open.

`GET /api/v1/agents/registry` publishes schema 1.0, PAPER mode, manifests, manifest references and server-owned lifecycle. No HTTP write, activation or permission-edit endpoint is provided. Local trusted composition may register versions or change lifecycle; models and tool payloads cannot claim lifecycle changes.

## Permission model

Fixed role ceilings in agent_permissions are immutable. A manifest may narrow them but cannot expand them. No ceiling contains broker submission, risk-policy writes, credential reads, production deployment or safety recovery. Unknown agent versions, unknown tools and scopes outside PAPER/SANDBOX fail closed.

QUARANTINED denies all tools. LIMITED admits only allowed reads. ACTIVE and SHADOW still obey the same fixed capability and resource-scope ceilings; ACTIVE does not confer live authority. Sandbox operations include training/holdout reads, candidate construction, validation/stress runs, replay and repair/evolution proposals. They cannot run in PAPER scope. Incident tools propose events; they do not apply safety transitions. Portfolio tools read or propose; they cannot issue kernel approvals or broker orders.

Generators A08/A09 and repair/evolution roles A16/A17 have no HOLDOUT_READ capability. Validators A10/A11 may receive it only in SANDBOX. This is a capability separation. Actual dataset isolation requires the future context compiler and trusted source adapters to bind dataset identity and prevent holdout access through alternate read paths; those adapters are not installed here.

## Tool boundary

AgentToolBoundary admits only known server-installed handlers after checking the exact agent version, current lifecycle, tool and scope. An uninstalled allowed tool returns unavailable. Capability listing reports only installed and currently allowed handlers. Permission denial happens before handler invocation.

Pass a frozen invocation context with agent ID/version, tool, scope and a JSON copy of payload. Reject reserved authority keys in payload. Input and output are bounded to 16/64 KiB, respectively; handlers have the manifest's cooperative timeout. Timeout cancels cooperative handlers, caller cancellation propagates, and underlying handler exception details are sanitized.

Handlers are trusted server code and must honor invocation scope and source permissions. This Python interface is not an operating-system sandbox and cannot stop hostile installed Python from importing a broker SDK or reading files. No production tool handlers or agent executors are wired by this increment. Process isolation, resource-specific authorization, task capability attenuation, admission budgets and durable tool-call traces remain requirements for the full runtime.

## Verification and rollback

Tests verify the exact 18-agent catalog, immutable registration, role-ceiling expansion rejection, all agents' denial on capital/control capabilities, generator/validator holdout distinction, scope and lifecycle checks, handler availability, denial-before-dispatch, bounded payloads, reserved authority fields, timeout cancellation and private-error sanitization. The API exposes PLANNED status and has no activation route.

Rollback removes registry composition and its read endpoint while leaving prior advisory results and audit history intact. No database migration or dependency change is introduced. Changes to a manifest require a new agent version and evaluation before an implemented agent can be promoted.
