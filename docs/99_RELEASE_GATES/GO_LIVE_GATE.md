# P13 restricted live rollout gate

**Current decision: NO-GO. Live execution and live credentials must remain disabled.**

This is the canonical P13 evidence register referenced by the environment strategy. It defines what must be reviewed before a separate, explicitly scoped release decision. It does not grant trading authority, evaluate evidence automatically, or replace independent review. A blank, stale, unavailable, unsigned, or unverifiable entry is not a pass.

## Current disposition

| Gate | Current state | Evidence recorded in this checkout |
| --- | --- | --- |
| P00-P12 phase acceptance | BLOCKED | `docs/BUILD_STATUS.md` records all phases in progress; no complete signed acceptance pack is present. |
| Release owner and independent reviewers | BLOCKED | No production/live release owner is assigned. No independent critical-path approval is recorded. |
| Eligible broker, account, instruments, and jurisdiction | BLOCKED | No live broker/account credentials or eligibility approval is present. Demo observation and simulator evidence do not establish live eligibility. |
| Production identity, secrets, and infrastructure | BLOCKED | Production identity/MFA, secret manager, deployment, network controls, and rotation evidence are not established. |
| Risk limits and capital scope | BLOCKED | No signed account-specific capital, leverage, instrument, exposure, or loss limits are recorded. |
| Monitoring, incident response, and stop controls | BLOCKED | Production monitoring and independently rehearsed broker-side stop/flatten procedures are not accepted. |
| Rollback and reconciliation drill | BLOCKED | No broker lifecycle reconciliation or independently witnessed rollback/flatten drill is recorded. |
| Signed release record | BLOCKED | No complete evidence pack or dual approval exists. |

## Required evidence gates

Every row must have a named accountable owner, reviewer, immutable evidence reference, source/build identity, capture time, expiry/review date, and explicit PASS. Evidence must be specific to the target account, environment, instruments, and exact release revision. The independent reviewer must be separate from the implementer and release operator.

| ID | Gate | Minimum acceptable evidence | State |
| --- | --- | --- | --- |
| P13-01 | Upstream phase acceptance | Current signed P00-P12 acceptance records, linked to tested source revision and current deployment artifact; all open findings dispositioned. | NOT RUN |
| P13-02 | Independent critical review | Independent security, architecture, risk, operations, and release review with reviewer identities, scope, findings, and signed conclusions. | NOT RUN |
| P13-03 | Legal, jurisdiction, and broker eligibility | Written approval for the exact entity, beneficial owners, jurisdiction, broker product, account type, instruments, data rights, and intended automated-trading use. | NOT RUN |
| P13-04 | Production ownership and dual control | Named service, safety, release, incident, and rollback owners; MFA-backed identities; least privilege; two-person approval and tested access revocation. | NOT RUN |
| P13-05 | Secrets and infrastructure | Isolated production secret store and identities; rotation/revocation drill; TLS/network/egress review; pinned reproducible artifact; backup/restore and retention evidence. | NOT RUN |
| P13-06 | Account and instrument scope | Exact broker/account identifiers (redacted in the pack), approved instruments, trading hours, order types, and broker-side permissions; verified against broker records. | NOT RUN |
| P13-07 | Risk and capital ceilings | Signed numeric account-specific limits for capital, leverage, per-order and aggregate exposure, daily loss, drawdown, concentration, and operating hours; tested fail-closed enforcement. | NOT RUN |
| P13-08 | Broker lifecycle and reconciliation | Independently witnessed tests for submit, duplicate/retry, partial fill, rejection, timeout/unknown, cancel, reconnect, restart, open positions, cash, fees, and reconciliation mismatch. | NOT RUN |
| P13-09 | Monitoring and incident response | Production telemetry with tested stale-source/failure alerts, on-call coverage, incident contacts, escalation timing, audit retention, and no silent-zero behavior. | NOT RUN |
| P13-10 | Stop, disable, and flatten | Broker-side credential revocation/disable and position flatten procedure tested by the designated human owner, including out-of-band access and evidence of completion. | NOT RUN |
| P13-11 | Rollback and recovery drill | Revision-pinned rollback, database/audit preservation, uncertain-order handling, restart recovery, and post-rollback reconciliation rehearsed without deleting evidence. | NOT RUN |
| P13-12 | Bounded canary and expiry | Separately approved, tightly capped initial scope, explicit start/stop window, automatic expiry to disabled state, observation period, and expansion prohibition without new approval. | NOT RUN |
| P13-13 | Signed release decision | Complete evidence index and digests; release operator, independent approver, safety owner, and rollback owner signatures; decision, scope, conditions, and expiry. | NOT RUN |

## Evidence record format

For each gate, retain a record containing:

- Gate ID and `PASS`, `FAIL`, `NOT_RUN`, or `UNAVAILABLE` status.
- Evidence URI or controlled repository reference plus SHA-256 digest; do not store credentials or unredacted account secrets in this pack.
- Exact source commit, build artifact digest, configuration revision, target environment, and account scope.
- Capture timestamp, validity/expiry date, evidence owner, independent reviewer, and signatures.
- Test method, observed result, limitations, unresolved findings, and rollback/stop condition.

Signatures must cover the evidence index digest and release scope so evidence cannot be swapped after approval. Any source, configuration, account, instrument, or risk-limit change invalidates the affected approvals and requires reevaluation.

## Decision rule

The only permissible current decision is **NO-GO**. A future decision may be considered only when every gate above is `PASS`, the evidence is current and independently verified, and the release record is signed by the distinct required human owners. A passing local test suite, simulator, backtest, paper/shadow run, demo account, UI state, model output, environment variable, or this checklist alone can never authorize live orders.

If any item fails, expires, becomes unavailable, or cannot be reconciled, retain **NO-GO**, disable new risk, preserve all order/audit/recovery records, and have the named human safety owner direct any broker-side disable or flatten action. Software rollback alone does not prove broker-side safety.

## Release record (intentionally unissued)

| Field | Value |
| --- | --- |
| Decision | NO-GO |
| Source revision / artifact digest | Not supplied |
| Target environment | Not selected |
| Broker / account / instruments | Not approved; no live credentials present |
| Capital and risk limits | Not approved |
| Release owner | Unassigned |
| Independent approver | Unassigned |
| Safety / rollback owner | Unassigned |
| Evidence index digest | None |
| Approval signatures | None |
| Expiry / next review | Not applicable |

## Rollback

This checklist is documentation-only. Revert this file and the corresponding P13 status reference together if the gate definition changes. No runtime configuration, credentials, broker route, or trading authority is changed by this document.
