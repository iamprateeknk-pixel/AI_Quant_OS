# Local demo sync journal inspection - increment 0123

The existing service CLI accepts `--inspect-state` with its original private
configuration and state directory. It reads the current audited controller
revision and pending identity, gateway custody, projection coverage/counts and
pending receipt commitment/report code. It starts no capture or timer and
publishes no heartbeat. This supplies current journal state after one-shot
commands, whose previous heartbeat remains unchanged.

## Read path

`DemoSyncController.inspect_health()` verifies stored policy/audit agreement in
a read transaction. Failure returns no health or invented revision. The separate
`inspect_sync` module builds a bounded public projection: coverage timestamps,
receipt/deal counts, local quote context and fixed receipt codes. Native account
balances, deal payloads, login/server, terminal paths and credentials are omitted.
Order, portfolio and capture authority are fixed false; native attestation is
UNVERIFIED.

READABLE/PARTIAL/UNAVAILABLE describe local journal readability. READABLE can
include a halted controller or uncertain gateway custody, which their explicit
fields report. Clock comparison labels a stored observation later than the
inspection clock without editing it. FINISHED describes a retained receipt
commitment; expired facts can have a commitment while report fields are withheld
with STALE_RESULT. This is not a new native observation or repair approval.

Inspection requires all three existing journals before creating a state
directory, and takes the existing exclusive CLI ownership gate. Stop the normal
service before using this offline command. Direct library writers remain outside
that gate; component reads are separate snapshots, not an atomic multi-store
snapshot or distributed lease. Store constructors retain their SQLite journal
configuration and may manage sidecars; this is application-level read-only
inspection, not a promise of byte-immutable filesystem access. No application
audit event, capture reservation, cursor or pending identity is changed.

## Credential and launch behavior

Inspection uses an internal noncredential placeholder solely to construct the
original connection policy. Existing policy identities already exclude the
password, so a matching original configuration can inspect without a password
or native Windows launch flag. It authenticates no broker. Actual capture and
recovery commands continue to load the fixed password environment variable and
enforce their existing enabled enrollment/Windows/launch gates.

Inspection is mutually exclusive with recovery actions. Mutating revision or
reduction parameters are refused before state access. Missing journals report
INSPECTION_STATE_REQUIRED; active CLI ownership remains SERVICE_ALREADY_RUNNING.
Output is the exported inspection model, with exit 0 for READABLE and 2 for
partial/unavailable inspection. No suggested action is automatically admitted;
every recovery command rechecks its exact revision, identity and receipt rules.

## Evidence and remaining work

Checks cover current halt/revision, post-command state with retained heartbeat,
capacity receipt codes, withheld corrupted/closed stores, uncertain custody,
clock comparison, missing state and mutation-parameter refusal, continued
capture password enforcement, schema capture denial, and a real inspection CLI
against disabled existing journals. Fixtures and local journals only; no actual
SDK, terminal, real credentials or external broker is used.

Existing wire schemas and journal event formats remain unchanged. The new
inspection schema is additive, and inspection creates no new replay metadata.
Rollback preserves journals. Native completeness, custody resolution,
checkpoint migration, installed services, independent broker review, browser
acceptance and P13 live release remain pending.
