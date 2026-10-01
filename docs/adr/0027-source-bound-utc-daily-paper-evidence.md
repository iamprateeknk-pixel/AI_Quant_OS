# ADR 0027: Derive daily paper PnL from retained opening and closing checkpoints

Status: Accepted for local paper accounting evidence.

Cumulative attribution assumes empty inventory at the start of the entire ledger. A daily report must instead preserve opening inventory and exclude historical cash/fees already accrued. Boundary marks must be observed evidence rather than reconstructed fill prices.

Capture immutable server-owned paper ledger/quote checkpoints, then publish a closed UTC calendar day. Compute day cash plus closing inventory less opening inventory. Use common portfolio liquidation marks per instrument at both boundaries, with a fixed five-second observation/capture allowance and no future quotes. Reconcile strategy/version contributions in separate quote currencies. Withhold missing marked values while preserving exact cash/quantities; do not turn accounting completion into promotion authority.

Require unchanged pre-opening fill history and ownership, stored source checkpoints and source-recomputed reports. A separate SQLite archive serializes immutable publication, preserves matching retries and verifies typed canonical hashes. All API capture/publication/read routes reuse existing operator and paper audit checks. The base application has no native trading dependency for this accounting path.

Consequences: complete reporting needs timely boundary capture and fresh prices. Local quote and ledger snapshots do not share one transaction; quote timing remains an explicit gap and future fill capture fails. Manual paper inputs and host clocks are trusted. Late imports may require investigation and future restatement support; existing evidence is never overwritten. Automatic scheduling, financing, execution/shadow/agent aggregation, production ingress and independent attestation remain open. Rollback preserves the sibling evidence database/WAL; older code does not infer approval from its artifacts.
