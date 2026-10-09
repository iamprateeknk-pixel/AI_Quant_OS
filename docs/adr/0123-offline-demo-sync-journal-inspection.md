# 0123 - Offline inspection of current demo sync journals

Status: implemented locally; native and independent acceptance pending.

## Decision

Add an audited controller read method and a separate public inspection projection,
exposed as a mutually exclusive one-shot CLI mode. Require original existing
journals and CLI ownership. Return current revision/intent, component read states,
coverage/counts and retained receipt code/commitment without capture, mutation,
heartbeat replacement or raw account payloads.

Use a noncredential placeholder for configuration inspection, relying on the
existing password-excluding policy identity. Actual command credential/launch
admission is unchanged. Inspection grants no capture/order/portfolio authority.

## Consequences

Operators can inspect current durable state after lost or completed command
output. Corrupt state supplies no invented revision. Readability is distinct
from native readiness; component snapshots and SQLite sidecar behavior are
documented. Every recovery command still revalidates admission.

Existing schemas/events remain unchanged; the inspection schema is additive.
No SDK, terminal, credentials or broker was used for verification. Native,
custody, migration, deployment, independent, browser and P13 acceptance remain
pending. Rollback preserves journals and requires no event migration.
