# 0035 — Isolated repair candidate construction

Status: Accepted for local proposal-only P10 candidate construction.

## Decision

Bind a proposed repair to an actual immutable health incident and its exact
quarantined manifest/version. Permit only declared sources of the two partial
shadow missions. Preserve all role capabilities and create a strictly newer
proposed manifest without registering it. Require a clean committed Git baseline,
proposed diagnosis and bounded regression plan.

Use an isolated index and Git plumbing to build a deterministic commit whose
sole parent is the baseline. Record the complete dossier in that commit, keep a
`codex/repair-<identity>` branch and create a separate worktree. Publish the pinned
receipt atomically and adopt only exact existing artifacts on replay. Inspection
rechecks the incident, manifests, baseline parent, complete changed-path set,
ordinary-file modes, source hashes, branch pin and clean candidate worktree.

## Consequences

Candidate construction never executes replacement Python, changes production
source, releases quarantine, modifies safety state or merges a candidate. All
independent verification, shadow, canary and release gates remain pending. Git
provenance is not a behavioral verification certificate; a proposed version's
runtime envelope compatibility remains unverified. A16's autonomous diagnosis
and full agent-envelope mission remain PLANNED.

The existing health journal does not migrate. Candidate dossiers and receipts
use schema 1.0. Git refs, objects and files are trusted local evidence, not external
signatures. The retained baseline is a future rollback target, not implemented
deployment rollback automation. Single-owner storage, execution isolation, repair
workflow coordination, independent review and complete recovery gates remain
future work.
