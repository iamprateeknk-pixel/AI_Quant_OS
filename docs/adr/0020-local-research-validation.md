# ADR 0020: Local evidence-only research validation

## Decision

Build P08 first as a local pipeline over actual P03 registered/native bar execution. Hypothesis, dataset-scope and research-plan contracts remain pure domain values. Native evidence DTOs, scorecard checking, SQLite reservations, archived-data CLI and orchestration sit in the infrastructure layer. This extends the existing Python module layout without introducing a deployed research service or changing paper execution authority.

Predeclare candidate genomes, training objective, chronological partitions, costs and adverse stress. Select only on development observations. Persist the final selection before terminal holdout execution. Reserve dataset roles/intervals transactionally before training, preventing terminal holdout reuse, relabelling and later design exposure within the same registry. An exact completed retry returns archived evidence; an interrupted reservation requires investigation.

Archive every completed native attempt, failed/rejected outcomes and sanitized stage reasons. Validate measured scorecard entries against their stored native results. Keep Monte Carlo, adversarial, sandbox, independent review and shadow gates visibly pending. No output grants promotion or capital authority.

## Consequences

Cold-start fold accounts are reproducible but do not model a continuous portfolio, training warm-up or statistical evidence of an edge. The synthetic fixture verifies interfaces rather than alpha. Dataset-role enforcement is local to a shared trusted registry; process isolation, global catalogs, agent permissions, production observability, complete validation and independent review remain open. Reservations and holdout claims are retained conservatively until an explicit recovery/release workflow is implemented.

Research storage is separate from paper orders and controls. Rollback preserves the research database, immutable data/source archives and exported evidence. This decision does not clear P08 or P13 gates.
