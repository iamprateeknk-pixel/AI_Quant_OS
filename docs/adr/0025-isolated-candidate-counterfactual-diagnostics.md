# ADR 0025: Replay declared alternatives on frozen candidate holdouts

Status: Accepted for local diagnostic evidence only.

P09 requires counterfactual datasets and isolated runs. Existing candidate dossiers retain native research results, immutable genomes/source artifacts and, for research 1.2, original holdout bars. Reusing these inputs must not be confused with new validation or promotion evidence.

Declare bounded alternative configurations before reserving a replay. Retain the complete parent dossier and original source bars. Reproduce the native baseline first, then run alternatives in separate native engines. Verify source/configuration identity, synthetic execution prices, fill cash flows and PnL differences on decode. Stop on baseline mismatch. Keep all diagnostic outcomes outside candidate lifecycle and paper execution authority.

A separate SQLite archive provides owned once-only reservations, immutable request identity, cached completed results and uncertain interruption handling. Typed canonical hashes bind evidence to the reservation. Exports use existing immutable atomic publication. No new API or agent permission is introduced.

Consequences: results describe sensitivity on already-seen data. Native engine accounts isolate simulation state but do not sandbox arbitrary Python. Only current verified allowlisted built-ins execute; historical source bytes are evidence, not executable fallback. Legacy archives require original holdout input. Local host administration, external attestation, global quotas, forward shadow integration and reality-gap demotion remain outside this increment. Rollback preserves the sibling database/WAL and stops new diagnostic runs; it never changes parent candidate approvals.
