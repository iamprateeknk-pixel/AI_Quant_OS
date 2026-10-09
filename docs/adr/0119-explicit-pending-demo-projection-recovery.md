# 0119 — Explicit recovery of a halted demo projection

Status: implemented locally; native and independent acceptance pending.

## Decision

Add one recovery action, FINISH_PENDING_PROJECTION, requiring exact observed
revision and pending attempt identity. Execute under the controller dispatch
guard and write transaction. Consume existing verified gateway/projection
receipts only; never call capture or create another native attempt. Record the
successful request in a recovery audit event and validate that transition on
replay. Preserve the original history window and all withheld pending intent.

Expose this action through a one-shot mode of the existing service CLI, with
original-store preflight, existing enrollment launch gates and exclusive local
service ownership. The mode exits without starting the timer or publishing a
service heartbeat. Return structured sanitized results with denied authority.

## Consequences

An interrupted projection can complete without duplicate native calls. Existing
receipt expiry and commitment rules remain enforced. The projection/controller
commit boundary is recoverable through ALREADY_APPLIED. Successful duplicate
commands conflict on revision. Existing wire schemas/tables are unchanged; the
new recovery schema is additive. Older readers withhold histories containing the
new recovery metadata, so rollback preserves journals and stops launches.

This action does not resolve orphan custody, clock-only halts, historical gaps,
wrong account enrollment or audit capacity. Those require their own reviewed
recovery operations. Native acceptance, service deployment and P13 release stay
open. No SDK, credentials, terminal or real broker was used for verification.
