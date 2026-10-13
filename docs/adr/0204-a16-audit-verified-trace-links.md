# ADR 0204: Bind verified graph traces to A16 incidents

## Decision

Add A16 0.4.0 with a distinct `RepairCandidateV4` output. For the latest bounded health incidents, the protected PAPER context builder resolves agent failure subjects through `GraphRuntime.trace`, which reconstructs the trace from the audited graph ledger. The source binds each trace to its exact health incident ID, failure status, agent ID/version, and manifest reference. A16 returns incident-to-trace references, not trace IDs without their incident association.

## Failure behavior and limits

Missing graph runs produce PARTIAL trace coverage. Changed status or manifest pins refuse the context; a corrupt graph ledger remains an unavailable evidence error. At most eight incidents and trace links enter one context. No task or payload text is copied into the repair report. Code-test receipts remain unavailable, so A16 remains WAIT_FOR_EVIDENCE and cannot create, execute, or promote a repair.

## Verification

Tests exercise a real failed graph run, retained graph trace reconstruction, exact incident association, missing-run partial coverage, manifest mismatch denial, and preserved repair blockers. See `docs/evaluations/health-repair-agent-0204.json`.
