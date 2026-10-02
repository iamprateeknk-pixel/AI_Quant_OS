# Retained prospective evaluation protocols

P11 now retains bounded schema 1.0 datasets and measurement plans for A02/A07.
These are prospective declarations. The catalog does not execute an evaluator,
measure genome quality, assign reputation, activate source or promote an agent.
Both authority fields require literal `false`.

## Commands

```powershell
ai-quant-evolve --workspace data-local/evolution dataset-register --input dataset.json
ai-quant-evolve --workspace data-local/evolution protocol-register --input protocol.json
ai-quant-evolve --workspace data-local/evolution catalog-list
ai-quant-evolve --workspace data-local/evolution seed A02 --protocol <protocol-sha256>
ai-quant-evolve --workspace data-local/evolution protocol-export <protocol-sha256> --output exported-protocol.json
```

Replace placeholders with actual references. The module command is
`python -m ai_quant_os.evolution_cli`. Register/list output contains artifact
metadata without case input/expected contents. Invalid typed input and invalid
export paths refuse before creating storage. Export publishes immutable JSON
outside registry storage and preserves differing existing output.

`data/schemas/evolution-dataset.schema.json` and
`data/schemas/evolution-protocol.schema.json` are portable schemas. Admission also
enforces canonical inner JSON, byte limits, sorted unique inventories, metric
directions/methods, same-agent dataset binding and exact input exclusion.

## Artifact identity and persistence

- Dataset identity is `evolution-dataset:<sha256>` over all canonical artifact
  bytes, including input/expected values, provenance and access classification.
  Each case carries canonical JSON objects as strings: at most 16 KiB each,
  at most 128 cases and 1 MiB serialized dataset bytes.
- Protocol identity is the SHA-256 of all canonical plan bytes, including the
  retained dataset reference, fixed output contract, primary metric, each metric's
  direction/minimum improvement/method and maximum regression. There are at most
  eight sorted unique criteria and 8 KiB serialized protocol bytes.
- Metric methods are fixed declarations for exact correctness, binary Brier,
  maximum evidence age, success rate, p95 wall time, tool failure rate, actual USD
  usage and regime exact correctness. No method is implemented or dispatched here.
  Mission-specific input/output adapters, units/denominators, failure aggregation,
  observed timestamps/usage and independent acceptance remain necessary before
  producing a quality report.
  This schema does not pin evaluator/adapter source. Future executable acceptance
  must bind that implementation and its comparison shape; changed measurement
  semantics or dataset bytes require a new frozen version/reference rather than
  reinterpretation of an existing plan hash.
- A protocol must reference a retained dataset for the same agent. `seed
  --protocol` derives the primary objective and full plan hash. The library
  `require_protocol_genome` checks agent/output/objective equality. Existing
  opaque objectives remain readable and do not become typed plans automatically.
  The genome archive is a separate database; its ordinary reads do not perform
  a cross-catalog evaluation check.
- `evaluation.sqlite3` uses WAL, synchronous FULL and BEGIN IMMEDIATE writers.
  Body and exact audit binding commit together. Same content is idempotent;
  different content at the same artifact kind/name/version is refused. All reads
  and startup verify canonical identity, exact event binding, chain/head integrity,
  referenced datasets and training/holdout exclusion. Limits are 1,000 artifacts,
  32 MiB total body bytes and one bounded audit event per artifact.

## Dataset access and its limits

The library dataset reader asks the current caller-supplied agent registry for
`TRAINING_READ` or `HOLDOUT_READ` in SANDBOX scope. A09 cannot retrieve HOLDOUT;
A10 can; quarantine also prevents an otherwise permitted read. No HTTP handler,
operator session or authenticated actor binding is added. Trusted callers must
bind actor identity and registry state themselves.

Identical canonical input hashes cannot cross TRAINING/HOLDOUT, even if case IDs,
dataset names or expected labels change. This is an exact-input exclusion only.
It does not detect semantically equivalent encodings, near duplicates or overlapping
underlying market observations. The database owner can read stored cases directly;
physical separation, statistical independence and external immutability are open.

Resource class is an access classification. Provenance labels are caller-owned
declarations. Synthetic development data remains synthetic development data even
when someone marks its resource class HOLDOUT. Such a label provides no evidence
of a statistically held-out evaluation.

## Evidence and next work

The installed CLI retained two synthetic training recipe datasets, two prospective
plans and two source-bound original genomes under ignored
`data-local/evolution-protocols-0053`. Fresh processes verified schemas, plan exports,
objective identity, repeat registration/seeding and exact original source bytes.
These cases retain recipe inputs and expected fixture labels; executable
mission-specific adapters and full output comparison are pending. No case or
retained source was executed by this workflow.

Tests cover role/quarantine admission, renamed-input overlap, immutable version
conflict, restart identity, missing/foreign dataset refusal, invalid criteria,
corrupt/deleted bodies/head, valid-chain wrong audit binding, concurrent idempotence,
audit-failure rollback, canonical/duplicate/nested JSON refusal, portable schemas,
real CLI preflight/seeding/export and preserved conflicting exports.

Full evaluation needs deployed-source provenance, observations for elapsed time,
tool failures and actual usage, verified isolated candidate execution, reviewed
datasets/adapters and independently checked reports. Existing graph records do not
retain deployed source or execution elapsed time; no retrospective genome score is
assigned to them. Promotion/shadow/canary/rollback, generation, scheduling and all
broader full-build gates remain open. Hosted Windows/Linux CI is configured but
unverified.

Rollback: preserve the separate evaluation database and SQLite/WAL/SHM files,
disable the new catalog commands, and use existing genome archival commands.
No runtime, broker, safety, sealed artifact or existing archive migration is needed.
