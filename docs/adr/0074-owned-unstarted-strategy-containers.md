# ADR 0074: Owned unstarted strategy create and cleanup controller

## Status

Implemented and tested against simulated daemon observations; actual Docker Linux
engine verification remains unavailable. No worker start or strategy execution.

## Decision

Connect the original archived package and staged request to a bounded immutable
create recipe and a permanent create-attempt marker. Reserve the existing audited
owner before any daemon request. Recheck original image/engine/source/env pins,
fsync an exclusive marker, issue at most one create RPC and inspect the returned
immutable ID before created-state admission. Keep execution/promotion false.

Use the fixed bounded Docker transport. Require complete strategy owner labels
and a fixed program/mount/resource recipe, reusing the existing common resource
admission predicates. Unknown/active created configuration refuses admission.

For recovery, use independently retained ownership/create hashes and the original
package/stage hash. Cleanup inspects only the exact owner ID or prepublished name,
records removal intent and removes by inspected immutable ID. Only an exact removal
acknowledgement records completion. Unknown absence never clears custody. Inputs
and sidecars remain even after removal; no automatic retries or shared-image cleanup.

## Limits

The controller performs no start/run/exec, output collection, proposed compilation
or tests. Created metadata is not running kernel evidence. The Docker Linux endpoint
is missing, so actual runtime defaults, mount translation and create/remove behavior
remain unverified. Filesystem and SQLite publication can retain partial state; local
host/namespace and cooperative root writers are trusted. Independent immutable audit,
power-loss directory-entry durability, full runtime execution/recovery, custody
release, source/behavior attestation and all promotion/production gates remain open.
