# Operator completion of a halted demo projection — increment 0119

## Recovery operation

`DemoSyncController.recover_pending(SyncRecoveryRequest)` finishes an explicitly
selected halted projection using its existing receipt. The request fixes action
`FINISH_PENDING_PROJECTION`, the observed controller revision, and the exact
pending UUID4. The result is RECOVERED, WITHHELD or DISABLED with fixed codes,
optional controller health, and denied order/portfolio authority.

The controller acquires its dispatch guard and a write transaction, validates
its full audit history and policy binding, then checks halted state, revision,
attempt identity, clock monotonicity and remaining transition capacity. A stale
request, changed state or wrong attempt returns STATE_CONFLICT. A busy same-object
controller returns BUSY. No denial clears intent or changes controller history.

Only `SyncProjectionStore.apply` is called, using the stored original window and
connection policy. Its gateway calls replay an existing receipt or compare an
already imported receipt commitment. Recovery never calls gateway capture,
reserves a new UUID, invokes the SDK, reconciles a child or substitutes credentials.
A missing, unfinished, uncertain or withheld receipt remains withheld. An expired
unprojected receipt cannot be imported. An already projected receipt can complete
after expiry using its original verified commitment, without presenting its
account/quote facts as fresh.

Success appends an audited recovery transition carrying the exact recovery
request, moves health to HEALTHY, clears the completed pending identity, advances
the cursor to that original window end, and schedules the next normal interval.
`last_success_at` is the projection/controller completion time; it is not a new
native capture time. Recovery is not a claim of current broker connectivity or
complete native history. A later service launch performs normal due work and can
halt on a history gap; recovery does not skip that gap.

Projection and controller commits remain separate. If controller persistence
fails after projection commit, the halted intent remains, and the same explicit
request can finish later through ALREADY_APPLIED. After successful controller
commit, repeating the old revision conflicts and creates no duplicate projection.
The recovery request is retained in the audited event, and replay checks its
relationship to the prior halted state and resulting health.

## One-shot CLI

The existing `ai-quant-demo-sync` entry point (or its equivalent module command)
accepts these additional paired arguments:

```text
--recover-pending-attempt <pending UUID4> --expected-revision <observed revision>
```

Use the existing private configuration and original state directory. Enabled
configuration retains the existing Windows, environment password and explicit
`--enable-demo-service` gates. Supply the exact revision/attempt shown in the
existing protected heartbeat or trusted journal inspection. Do not invent them.
The one-shot command requires all three existing journal files before creating
any state, acquires the existing exclusive service gate, opens the original
stores, performs recovery and exits: 0 for RECOVERED, 2 otherwise. It does not
start the timer or update `health.json`; that heartbeat remains the previous
service publication until an explicit normal service launch. Its JSON result
reports the recovery outcome and controller health. Invalid paired arguments
produce RECOVERY_INVALID; missing original journals produce RECOVERY_STATE_REQUIRED.

## Verification and limits

Checks cover first import, already imported expired receipts, restart, duplicate
requests, stale/wrong identities, pending unknown custody, wrong native account,
clock regression, capacity, corrupt controller audit, persistence failure after
projection commit, disabled/busy operation, CLI existing-state and validation
requirements, one-shot composition, and public schema order denial. Native
fixtures and the existing disabled service subprocess are used; no native SDK,
terminal, credentials or external broker is exercised.

Recovery is currently completion of a verified pending projection. Clock-only
halts, missing-history backfill, archive/checkpoint migration, native/orphan
custody reconciliation, failed account enrollment, installed Windows services,
remote controls, independent broker review and P13 live acceptance remain open.
There is no reset, automatic orphan adoption, worker retry or live order authority.

## Compatibility and rollback

All existing public schemas and controller tables remain unchanged. A new
recovery schema is exported. New successful recovery audit events carry optional
`recovery` metadata; earlier code rejects such events and withholds that store.
Stop the service and preserve all journals on rollback. Do not edit or erase the
recovery event to make an old version accept it. Returning to the current reader
restores access to intact audited history.
