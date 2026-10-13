# ADR 0202: Bind shadow reality-gap policy at assignment

Date: 2026-10-13

## Decision

An optional holdout-to-forward-shadow reality-gap policy is immutable assignment evidence. It is canonicalized and hash checked in a separate additive table keyed by shadow assignment. Existing assignments are migrated to an explicit disabled value; a policy cannot be retrofitted after observations begin. Repeating assignment requires the same policy bytes.

When an observed report meets the declared bar and fill floors and its adverse return gap meets the threshold, the candidate store independently recomputes the assessment from that retained report and appends a revision-checked, hash-linked `DEMOTED` event. Matching report retries are idempotent. Demotion prevents new shadow observations, while reports remain readable.

The metric compares the candidate's frozen native holdout net return with cumulative forward-shadow net return, each divided by its own filled gross notional. It is a diagnostic proxy, not a live-versus-shadow or broker execution comparison. No policy is enabled by default.

## Consequences

- The comparison policy cannot be selected after outcomes are observed.
- Legacy assignments keep their prior behavior and remain policy-disabled.
- The report/policy hashes and measured evidence are preserved in the candidate journal.
- PAPER-versus-shadow comparison, operator alert delivery, production ingress/scheduling and independent policy calibration remain open.
- The feature has no broker route authority and cannot promote a candidate.
