# ADR 0210: Audited agent lifecycle journal

## Status

Accepted for local persistent PAPER mode; independent production acceptance remains open.

## Decision

Persist every server-owned agent lifecycle transition in the sibling
`<paper-db-stem>.agent-lifecycle.sqlite3` database. Events bind the exact
`agent_id`, `agent_version`, immutable manifest reference, previous and next
state, and per-version sequence. Canonical event hashes are bound into the
database's append-only `AuditChain`.

At application construction, the registry verifies the lifecycle journal and
restores it before any route can serve the agent catalog or capabilities. An
unknown manifest version, changed manifest pin, invalid transition chain, audit
failure, or capacity exhaustion is a startup error. Unrecorded/new versions
start in `SHADOW`; they never inherit another version's state. Quarantine is
persisted as a terminal lifecycle transition and cannot be cleared through the
ordinary lifecycle setter. Health incidents remain a second source of
quarantine evidence, and watchdog restoration re-applies the exact pinned
quarantine.

The journal only applies when the API has a persistent PAPER database. Ephemeral
in-memory app mode starts from manifest defaults. SQLite is local to one
application identity and is not a distributed lease or an OS security boundary.
An attacker able to rewrite the full database, audit chain, and head can forge
history; independent storage permissions and review remain release gates.

## Migration and rollback

Existing deployments create the sidecar on startup. No prior lifecycle state
was durable, so absent journal entries restore the existing safe default,
`SHADOW`. Operators must preserve the sidecar with the PAPER and agent graph
databases during backup, restore, and code rollback. Older binaries ignore the
sidecar. Newer binaries must validate it before granting capabilities. Do not
delete or reset the sidecar to recover from corruption; restore a verified
backup or keep startup blocked and investigate.

## Verification

Tests cover transitions over restart, exact-version binding, new-version
defaults, tampered journal refusal, changed manifest refusal, persistent API
startup restoration, and quarantine precedence/pinning across restart. The
focused verification command is:

```powershell
python -m pytest -q tests/test_agent_lifecycle_store.py tests/test_agent_registry.py tests/test_agent_context.py tests/test_context_call_budgets.py tests/test_graph_runtime.py tests/test_health_watchdog.py
```
