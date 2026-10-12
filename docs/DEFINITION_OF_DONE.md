# Definition of Done

A change is complete only when its implementation, evidence, and remaining limits are reviewable. This definition applies to local paper work and does not grant shadow or live authority.

## Change completion

- The change maps to a phase/workpack requirement and keeps contracts and authority boundaries explicit.
- Versioned inputs and outputs reject unknown fields, malformed values, invalid time/currency units, and unsupported authority claims where those contracts apply.
- Critical decisions have one deterministic owner listed in [CONTROL_OWNERSHIP.md](CONTROL_OWNERSHIP.md). AI and UI layers cannot bypass that owner.
- Failure, stale, unavailable, timeout, replay, and recovery behavior is tested for the affected path; tests assert authoritative receipts rather than optimistic UI state.
- The narrow relevant test suite and required build/type/lint checks pass in the documented environment. Any unrun suite or unavailable integration is recorded as open.
- No secrets or private credentials enter tracked source, fixtures, logs, browser bundles, or artifacts. CI secret scanning and targeted review pass.
- Changes to persistent data include migration, compatibility, rollback, and evidence-retention behavior. Rollback never deletes uncertain orders, safety receipts, audit records, or execution reservations.
- Documentation records the decision, verification evidence, risks, rollback, and unresolved dependencies. A handoff names exact commands and file locations.
- Critical capital-affecting changes receive an independent review before phase promotion.

## Release completion

- Every applicable phase acceptance item has linked, current evidence; an unchecked, unverified, or blocked item is not a pass.
- P13 remains blocked until P05-P12 release gates pass, independent reviewers approve the evidence pack, and operational drills meet the P13 checklist.
- Paper, shadow, demo-account observation, historical simulation, and a passing build never establish live eligibility or authorize capital.
- Live enablement stays off unless the P13 release record explicitly identifies the eligible account/instruments, bounded capital and risk ceilings, broker permissions, monitoring, stop conditions, rollback/flatten owner, and expiry/review date.
- Scaling requires a separately approved policy and measured evidence; it is never automatic.
