# ADR 0068: Prefix-only candidate invocation inputs

## Status

Implemented input preparation and unattested target assembly; worker execution remains pending.

## Decision

Keep the entire validation package in the trusted parent. Build one immutable JSON envelope containing the exact candidate/SDK and two prior closed bars for each non-warmup decision. No worker input carries the full holdout, training data, review, plan, proposed tests or current/later prices. Warmup is parent-owned flat output.

Bind every response to the hash of the complete exact invocation. Require one response per non-warmup open and revalidate all inputs against the original package before assembling the existing unattested numeric tape. Fixed false authority and waiting/unattested statuses cannot be replaced with claimed execution, confinement or promotion proof.

This is an application boundary above the unchanged pure domain modules and beside the numeric replay adapter. It performs no candidate import, compilation, execution or filesystem materialization of candidate/SDK Python. An operator CLI exports only one inert request at a time and preserves existing output files.

## Consequences

Local self-consistency alone is insufficient: a future trusted launcher must use exact package binding, fixed stage inventory and an admitted kernel/runtime/image before execution. Worker-supplied hashes/status cannot establish independent source attestation. The existing validation package's static SDK capture and named SANDBOX scope do not establish confinement.

The future isolated worker must receive only its individual request and harness, with no parent archive or future data mounted, and use a new isolated lifetime per decision. Proposed tests require separate execution without holdout data. These execution/lifecycle requirements remain unimplemented and unverified while the fixed Docker Linux engine is unavailable. Statistical acceptance, independent review and promotion remain separate open gates.
