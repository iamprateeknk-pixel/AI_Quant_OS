# Research candidate registry

P08 remains in progress. Completed experiments can now be registered as immutable, portable research dossiers. This establishes the local challenger inventory and experiment lineage needed for subsequent governance.

## Dossier and lifecycle

A bundle contains a derived manifest, the complete typed research report and the exact retained source objects referenced by every completed native attempt. Candidate identity binds the experiment ID and canonical report hash; the manifest also binds the hypothesis, plan hash, frozen genome, outcome, reasons and pending/failed gates. Decoding verifies the report and source hashes, regenerates the manifest and rejects omitted native evidence. Source bytes are retained for inspection and are never executed by registration or export.

`VALIDATION_INCOMPLETE` experiments enter `CHALLENGER`; `REJECTED` and `FAILED` experiments retain those states and reasons. A failed experiment may have no frozen genome or source objects. `CHALLENGER` means inventory awaiting validation, with no approval to trade or enter shadow operation. `promotion_allowed` is always false. Champion comparison, nomination/assignment and a genuinely promotion-ready bundle remain unfinished, along with the underlying pending validation gates.

Registration is idempotent for identical experiment/bundle identity and rejects conflicting reuse. A local operator can withdraw a challenger with a bounded reason, command ID and expected revision. Withdrawal preserves the original bundle, advances revision once and records `WITHDRAWN`. A concurrent or stale command cannot overwrite the winning withdrawal; identical retries return its committed result. Failed/rejected records cannot be relabeled by withdrawal. An opted-in forward-shadow evaluator can also append `DEMOTED` when a declared minimum-sample holdout-to-shadow net-return gap crosses its adverse threshold. The journal event binds the retained shadow report hash, policy parameters, returns and threshold. Demoted candidates fail shadow lifecycle checks, while prior reports stay readable.

## Storage and integrity

- `candidates.sqlite3` is a new sibling of the research database in the configured workspace. The research, strategy and data archives retain their original formats.
- Registration and withdrawal commit with a sequence-checked SHA-256 event chain and committed head in the same SQLite transaction. Startup and operations verify candidate identities, bundle fingerprints, event continuity and head. A journal failure rolls back the candidate write.
- Reads use a database snapshot; competing writes use an immediate transaction. Duplicate concurrent registration stores one candidate and one event.
- Capacity: 1,000 dossiers, 64 MiB aggregate bundle bytes, 16 MiB per bundle, 5,000 events, and 1 MiB per retained source object. These local limits may require an explicitly reviewed retention/migration policy for large research volumes.
- These integrity checks detect corruption and inconsistent local history. A host administrator can replace both records and hashes. They do not constitute authenticated external attestations or a security boundary against that administrator.
- Preserve the database and its WAL before rollback. Older code ignores this new sibling database; it must not be used to infer approval from registry membership.

## Command line

Install the existing research prerequisites and the editable package, then use:

```powershell
ai-quant-candidates --workspace data-local/research-adversarial-a register adversarial-baseline
ai-quant-candidates --workspace data-local/research-adversarial-a list
ai-quant-candidates --workspace data-local/research-adversarial-a bundle CANDIDATE_ID --output data-local/candidate-bundle.json
ai-quant-candidates --workspace data-local/research-adversarial-a withdraw CANDIDATE_ID --expected-revision 1 --command-id retire-fixture --reason "Retire synthetic evaluation"
```

Bundle output is an immutable atomic file: exact repeats succeed, changed existing content is refused. Database/WAL/SHM paths and archive/strategy subtrees cannot be output targets. Export does not reexecute native backtests. Report decoding still verifies stored Monte Carlo and scenario statistics. Raw CSV/Parquet market data are not embedded in the bundle; retain the original verified workspace archives needed for replay.

## API

Set `AI_QUANT_RESEARCH_WORKSPACE` to a local research workspace and configure the existing operator token. All routes below require `X-Operator-Token` and valid paper audit integrity. Callers supply experiment IDs, not reports, filesystem paths, promotion claims or source code.

| Route | Purpose |
| --- | --- |
| `POST /api/v1/paper/research/candidates` | Register `{ "experiment_id": "..." }` from server-owned archives. |
| `GET /api/v1/research/candidates` | List manifests, lifecycle, revision and latest event hash. |
| `GET /api/v1/research/candidates/{id}/bundle` | Retrieve the verified canonical bundle, preserving nested legacy report hashes. |
| `POST /api/v1/paper/research/candidates/{id}/withdraw` | Supply expected revision, command ID and reason. |

Unknown bundle IDs return 404. Invalid inputs return 422; registration/lifecycle conflicts return 409. Disabled workspace, unavailable native prerequisites, storage failures and registry verification failures return 503. Reads are protected because the dossier includes research/holdout evidence and source. No broker, risk approval or agent tool permission is added. Native imports are deferred so the default paper API still runs without the trading extra.

## Evidence

The committed `docs/evaluations/research-candidate-bundle.json` derives from the synthetic 1.2 adversarial baseline, includes 14 native attempts and three retained source objects, and preserves its original report hash. Two independent registries exported identical bytes; typed decoding, canonical reencoding and `docs/schemas/research-candidate-bundle.schema.json` validated. This proves dossier construction and retention for the fixture, while its sandbox, independent review and shadow gates remain pending.
