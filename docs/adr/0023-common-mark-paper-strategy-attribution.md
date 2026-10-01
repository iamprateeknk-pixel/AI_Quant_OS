# ADR 0023: Common-mark paper strategy attribution

Status: Accepted for cumulative local accounting; P09 remains incomplete.

Read original strategy ownership and fills atomically from the paper store. Verify record identities and the existing accepted-order quantity/time constraints before grouping. Bind report lineage to original intent/order hashes and the retained fill snapshot.

Use one liquidation-side mark per instrument, chosen from the portfolio's net position, for all strategies. Reconcile grouped quantities, cash before fees, fee cash flow and marked P&L against the instrument ledger. Keep quote currencies separate. Preserve strategy versions and partial fills. Missing/untrusted/stale/future marks withhold marked values without losing cash accounting.

Do not infer missing causal labels or decision-price execution benchmarks. Existing paper intents do not bind resolved candidate genomes; agent, candidate-shadow and causal attribution remain absent. The protected read API cannot approve trading or promotion. Durable daily evidence and the rest of P09 require additional retained inputs and workflows.
