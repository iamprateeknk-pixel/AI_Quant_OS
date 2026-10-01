# ADR 0021: Declared native adversarial evidence

Status: Accepted for local evidence-only research; P08 completion and release gates remain open.

The strategy must be frozen before adversarial holdout execution. Contract 1.2 registers the deterministic scenario policy and currency/loss budget with the immutable plan before training. It executes the two perturbed datasets using the same native strategy/source binding, quantity and declared stress costs. Two extra executions and their bar visits count against existing resource budgets.

Retain original holdout bars so archive decoding can regenerate scenario fingerprints. Bind scenario runs to archived native attempts, validate fill quantity/fees, reconcile position and cash flows plus final marking, and recompute the screen. Failed native execution retains prior attempts and a sanitized stage. Loss-budget violation rejects; no activity leaves the screen pending. Passing either screen cannot authorize promotion.

Do not retrofit these checks to old holdouts. Existing 1.0/1.1 plans and reports keep their original byte encoding and pending adversarial gate. New policy requires a newly reserved experiment and unused data under the local chronological claim rules.

Missing observations and one downward step provide partial scenario coverage. They do not prove feed/control behavior, intratrade drawdown, statistical validity or live readiness. Depth, spread, liquidity, latency, additional shocks/regimes, sandbox, independent review and shadow validation remain required.
