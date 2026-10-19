# Environment strategy and promotion boundaries

## Purpose

This document records the required environment sequence for AI Quant OS. It is a build and release plan, not confirmation that a particular deployment was selected or provisioned. The current implementation and evidence remain local and paper-only. No AWS account, deployment, provider spend, broker connectivity, or live capital is authorized by this plan.

## Environment tiers

| Tier | Intended use | Allowed authority | Entry evidence | Current status |
| --- | --- | --- | --- | --- |
| Local development | Code changes, unit/integration tests, small offline backtests, and UI work | Synthetic/local data and the deterministic paper simulator; no live broker credentials | Reproducible pinned environment, isolated tests, secret-free fixtures, local operator boundary | Available in this workspace; local verification is not independent acceptance |
| AWS development/staging | Shared integration tests, isolated databases, and broker demo gateway evaluation | Non-production accounts and explicitly scoped demo credentials; no live orders | Named owner and region, network/data-flow review, secrets and rotation plan, budget alerts/limits, retention and restore tests, authenticated access, teardown plan, and P00/P04/P12 review | Not selected, provisioned, or accepted |
| Shadow/paper | Continuously observe market/data/agent behavior with no live-capital authority | Paper orders and read-only demo observations; never live execution | Staging gate passed, bounded model/provider spend, monitoring and incident recovery rehearsed, data licensing/compliance review, independent paper acceptance, rollback/stop procedure | Local paper components exist; continuous operation and phase acceptance are not established |
| Restricted live | Isolated execution core with explicit broker enablement and strict capital/risk ceilings | Live trading only after signed release gates and independent authorization | All P00–P12 accepted, jurisdiction/account/vendor approval, production identity and dual control, secrets isolation, tested kill/rollback, reconciled broker lifecycle, validated risk ceilings and capital limits, independent critical review, signed P13 gate | Disabled and blocked; no release owner or implementation exists |

## Cross-environment controls

- Secrets belong in the environment's secret store. They must not enter source, fixtures, prompts, logs, reports, or build artifacts. Local development uses synthetic credentials or test doubles; remote demo credentials require an approved staging plan and rotation owner.
- Each environment has separate identities, databases, encryption keys, network policy, and storage. Production credentials and data cannot be copied into development or staging.
- Runtime model/provider calls require per-role daily and monthly budgets, cost visibility, and a protected incident-recovery reserve. Cheap/fast routing and interruption of batch research are policy goals; actual ceilings, billing correspondence, and provider-price assumptions still require acceptance.
- Dependencies and build outputs remain pinned and attributable. Moving between tiers requires reproducible artifacts and evidence; environment configuration cannot override paper/live release policy.
- Staging or shadow failure must stop promotion and retain evidence. Recovery must be rehearsed against the same environment and must not erase audit, order, or incident records.
- Cloud and vendor pricing, data fees, and compliance obligations are not fixed here. Recheck authoritative terms before purchase or external deployment.

## Promotion and rollback

Promote only after the current tier's entry evidence is independently checked. A failed gate returns the system to the previous non-live tier, disables new risk, preserves original records, and records the reason and evidence. P13 remains the sole live-release authority; an environment variable, UI state, model output, broker-health observation, or shadow result cannot enable live execution.

Current promotion evidence is represented by the fail-closed `PromotionDecision` v1 contract. It always returns `WAIT_FOR_EVIDENCE` with `approved_by_policy=false`; it does not evaluate gates or change lifecycle. See [CONTRACT_VERSIONING.md](CONTRACT_VERSIONING.md), [RELEASE_AND_ROLLBACK.md](RELEASE_AND_ROLLBACK.md), and the canonical [P13 restricted live rollout gate](99_RELEASE_GATES/GO_LIVE_GATE.md).

## Decisions still required

The operator must confirm intended deployment model, service owner, expected users, and internet exposure before any staging design is treated as final. Security, legal/compliance, data rights, broker eligibility, privacy/retention, and cost owners must approve their respective gates before external data, remote services, or live capital are introduced. No such approval is inferred here.
