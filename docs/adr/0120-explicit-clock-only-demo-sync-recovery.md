# 0120 - Explicit recovery of a clock-only demo sync halt

Status: implemented locally; native and independent acceptance pending.

## Decision

Add RESUME_CLOCK_HALT as a separate strict request/result contract and one-shot
service CLI action. Require exact halted revision, CLOCK_REGRESSION reason, no
pending attempt, caught-up time, retained original coverage, capacity, verified
gateway worker receipts and projection/cursor agreement. Append an audited clock
recovery transition under the controller write transaction and dispatch guard.

Return to STARTING and preserve history cursor, last success and failure count.
Keep the later of current time and original due deadline. Do not capture,
project, create an attempt, reset journals or manufacture native facts.

## Consequences

An operator can repair time and resume scheduling without bypassing uncertain
work, backoff or missing coverage. Replay validates the recovery transition.
Normal later ticks retain the existing gateway admission checks. Separate
journal inspections are snapshots, not a distributed lease or native custody.

Existing public schemas and tables are unchanged. The new schema is additive;
older readers withhold clock recovery audit events. Rollback stops launchers and
preserves the journals. Historical backfill, orphan custody, installed services,
native and independent acceptance remain pending. Verification uses local
journals and fixtures, with no actual SDK, terminal, credentials or broker.
