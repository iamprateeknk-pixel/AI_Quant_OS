# 0046 — Publish staging ownership before writing inputs

Status: Accepted for local source-bound orphan cleanup.

## Decision

Publish an exclusive fsynced staging receipt binding original root identity and
independent source/suite/request hashes before input writes. Preserve that root
identity through preparation. Add a root-locked cleanup command that reconstructs
expected inputs from independent pins, verifies every survivor, refuses pending
container ownership or unfinished journal outcomes, deletes inputs, then releases
the receipt. Remove ordinary staging receipts before terminal container cleanup.

## Consequences

Real source/file and fresh-process CLI cases verify orphan cleanup without daemon
or candidate execution and conservative tamper/ownership refusal. Complete input
inventory remains mandatory for sandbox admission. The pre-receipt allocation
window, torn writes, power-loss/process-death evidence, POSIX permissions, actual
confinement and broader repair/release gates remain open. Preserve receipts and
inputs before rollback; old cleanup may leave new control files behind.
See `../REPAIR_STAGING_HANDOFF.md`.
