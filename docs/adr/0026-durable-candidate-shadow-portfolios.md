# ADR 0026: Preserve cumulative shadow portfolios through causal native replay

Status: Accepted for local diagnostic tracking only.

P09 requires shadow portfolios bound to candidate evidence and isolated from broker authority. Native research already has immutable dossiers, verified allowlisted implementations and a causal long/flat bar model. Native engine process state is not a durable public persistence contract.

Store a frozen challenger assignment with a post-holdout cutoff, then append immutable chronological observations. Reserve each batch before executing a fresh native engine over the cumulative source series. Verify that the prior fill prefix reproduces and that new fills occur only in the new period. Retain exact source, native results, sequence/hash lineage and a committed head in a separate SQLite database. Failures and unfinished reservations stop further work; completed commands are cached.

The local command chooses the current assignment clock. Explicit logical cutoffs remain available to trusted library callers for historical fixtures and cannot establish contemporaneous forward evidence. Candidate eligibility is read atomically with its bundle at assignment and rechecked before/after observations. Separate SQLite authorities leave a withdrawal/archive race without capital permissions; unified transactional lifecycle control remains future work.

Consequences: cumulative source replay avoids serializing private native internals, isolates accounts and supports restart, but repeats work, has bounded retention and starts flat without historical warmup. Source/price/fee/cash verification is shared with counterfactual evidence. No paper approvals, broker routes, candidate promotion, research gate updates or agent permissions result. Production ingress, recorded execution realism, lifecycle recovery, shadow scoring and independent review remain open. Rollback preserves the new database and WAL; earlier systems ignore this sibling and cannot infer approval from its contents.
