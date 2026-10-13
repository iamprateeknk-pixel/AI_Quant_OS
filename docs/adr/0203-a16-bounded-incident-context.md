# ADR 0203: Add bounded incident context to A16

## Decision

Preserve A16 0.2.0 and introduce A16 0.3.0 with a distinct `RepairCandidateV3` output contract. The new version reads the verified health incident journal and binds at most the eight most recent incident identities, failure codes, detection times, and exact agent manifest pins. It excludes incident subjects and raw evidence. Missing and truncated incident histories remain explicit blockers.

## Safety boundary

A16 0.3.0 remains PARTIAL/SHADOW and deterministic. Trace archives and code-test receipts are still unavailable, so it always returns `WAIT_FOR_EVIDENCE`, creates no repair candidate, runs no verifier or replacement code, changes no health state, and cannot promote. The existing isolated repair-candidate CLI remains separate.

## Verification

The API reads only `HealthStore.incidents()`, which verifies the audit chain and every retained incident before returning records. Incident detail context is capped at eight entries. Tests cover bounded detail projection, raw evidence and subject omission, unavailable details, protected graph execution, and preservation of the 0.2.0 contract.
