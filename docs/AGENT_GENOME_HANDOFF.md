# P11 immutable agent genome registry

The new local archive retains a typed schema 1.0 genome with the exact original
agent manifest, owned UTF-8 source bytes, optional prompt/model reference,
declared feature references, parent and predeclared objective. Its
`agent-genome:<sha256>` binds canonical complete artifact bytes. The saved portable
schema is `data/schemas/agent-genome.schema.json`; additional source ownership,
semantic-version, role inheritance and metric-direction rules run at admission.

## Commands

```powershell
ai-quant-evolve --workspace data-local/evolution seed A02 --objective objective.json
ai-quant-evolve --workspace data-local/evolution register --input challenger-genome.json
ai-quant-evolve --workspace data-local/evolution list
ai-quant-evolve --workspace data-local/evolution lineage agent-genome:<sha256>
ai-quant-evolve --workspace data-local/evolution export agent-genome:<sha256> --output exported-genome.json
ai-quant-evolve --workspace data-local/evolution measure --protocol <protocol-sha256> `
  --baseline-genome <baseline-ref> --challenger-genome <challenger-ref> `
  --outputs observed-outputs.json --evaluated-at 2026-10-14T00:00:00+00:00 `
  --output scorecard.json
```

Replace placeholders with actual retained references/paths. The module form is
`python -m ai_quant_os.evolution_cli`. Seed accepts an objective JSON containing:
schema version 1.0, a supported metric, its fixed MAXIMIZE/MINIMIZE direction,
finite nonnegative minimum improvement and a 64-character lowercase protocol SHA.
Metric names are correctness, calibration error, evidence age, reliability,
latency, tool-error rate, USD cost, regime correctness and integrity correctness in their explicit
uppercase wire forms. Improvement for rate metrics is bounded to one.

The legacy `--objective` path retains a caller-owned opaque predeclaration.
`--protocol` instead derives the exact objective from a verified retained same-agent
plan in the evaluation catalog. Neither path proves actual evaluation or grants
release authority. Model and feature configuration is descriptive and never dispatched.
See `EVOLUTION_PROTOCOL_HANDOFF.md` for the new dataset/plan commands and limits.

Use `execute-training` to produce actual outputs before measuring retained
baseline and challenger genomes:

```powershell
ai-quant-evolve --workspace data-local/evolution execute-training `
  --protocol <protocol-sha256> --baseline-genome <baseline-ref> `
  --challenger-genome <challenger-ref> --output training-outputs.json
ai-quant-evolve --workspace data-local/evolution measure --protocol <protocol-sha256> `
  --baseline-genome <baseline-ref> --challenger-genome <challenger-ref> `
  --outputs training-outputs.json --evaluated-at 2026-10-16T00:00:00+00:00 `
  --output scorecard.json
ai-quant-evolve --workspace data-local/evolution evaluate-training `
  --protocol <protocol-sha256> --baseline-genome <baseline-ref> `
  --challenger-genome <challenger-ref> --evaluated-at 2026-10-17T00:00:00+00:00 `
  --output executed-scorecard.json
```

`evaluate-training` runs the captured baseline and direct child in separate
containers, scores their returned outputs against the retained training protocol,
and retains the diagnostic scorecard. Its result remains explicitly unverified.

## Admission and persistence

- Seed bootstraps only actual installed A02/A07 0.2.0 manifests and their declared
  source files. A forged/edited root cannot register as a trusted original. Roots
  are `ARCHIVED_BASELINE`; runtime catalog/lifecycle/quarantine is untouched.
- Descendants are `UNVERIFIED_CHALLENGER`, with a retained exact parent, same
  agent, strictly newer canonical semantic version and unchanged objective.
  Role, required inputs, output contract, mission and implementation status stay
  fixed; tools and timeouts can only shrink relative to the immediate parent.
  A new version alone is insufficient; source/config/capability changes are needed.
- The exact sorted editable source inventory excludes risk, kill-switch,
  credentials, execution/idempotency, reconciliation and deployment files.
  This prevents direct edits to those files; it is not a Python behavior or
  import-policy certificate. The training runner overlays genome-owned source
  bytes onto a read-only package snapshot (ordinary single-link files only,
  capped at 2,048 files and 32 MiB) and runs each genome in a fresh offline
  container from pinned image
  `sha256:5c86773e1c259ac6b89d6ff2bf8ceca386de0013b66c4e3b0cd1f1e91658e5b4`.
  It accepts ready SANDBOX contexts classified TRAINING only and omits expected
  outputs from candidate requests. This evidence supports training diagnostics;
  it is not an independent isolation certificate or release authorization.
- Frozen typed values and canonical bytes bind identity. Registration revalidates
  caller objects. SQLite BEGIN IMMEDIATE serializes writers; body and matching
  audit event commit together with synchronous FULL and WAL. Same content/version
  is idempotent; different content at the same agent/version is refused.
- Every read/startup checks body identity, complete parent policy and exact event
  binding against a verified hash chain/head. Limits are 1,000 records, 256 KiB
  per serialized genome, 32 MiB total body bytes and one bounded event per genome.
  Sources are bounded to 64 KiB UTF-8 each and prompts to 32,768 characters, with
  the final serialized limit applied after JSON escaping.
- Both authority fields require literal `false`; registration and export never
  run retained Python, activate an implementation, call a provider or promote.
  Export publishes immutable bounded JSON outside the registry using a temporary
  file, fsync and atomic hard-link publication. Existing differing bytes remain.

Existing genome archives remain readable after installed source changes; fresh
seed at an already recorded version refuses changed bytes. The source reader checks
ordinary single-link final files, but trusted host/package/directory ancestry is
still required. Local hashes/audit are not signed independent storage or protection
from an administrator rewriting the complete database/head. Database file size,
power-loss restoration, retention/rotation and distributed storage remain open.

## Current evidence and remaining work

The installed command created two actual source-bound roots and one inert feature
subset challenger under ignored `data-local/evolution-registry-0052`. Fresh process
reads verified exact source bytes, saved schema, root idempotency and parent lineage.
Its protocol declarations explicitly identify synthetic development fixtures and
unfinished isolated execution; no quality result or improvement was fabricated.

## Deterministic training diagnostics

`ai-quant-evolve measure` compares caller-recorded baseline and direct-challenger
outputs against the complete ordered case inventory in a retained `TRAINING`
dataset. It loads the dataset through the A09 `TRAINING_READ` capability, checks
the protocol and both genome objective pins, verifies direct parent lineage, and
supports exact-object correctness, the A02 regime-label adapter, and the A07
integrity-evidence adapter. Unsupported measurement methods, holdout data,
missing/extra/reordered cases, and mismatched references are refused.
The caller must supply an explicit timezone-aware `--evaluated-at` timestamp so an
exact retry produces the same immutable scorecard. The input contract is
`data/schemas/evolution-output-bundle.schema.json`; the report contract is
`data/schemas/evolution-scorecard.schema.json`.

`execute-training` supports A02 and A07 with the pinned local Docker image. It
requires the captured server-owned root genome and a direct child whose source
is overlaid only inside the container. Its output bundle remains explicitly
unverified; no lifecycle, promotion or production authority changes.

Scorecard schema 1.3 records exact-match counts, the full case denominator, and
one case-level row with baseline/challenger match booleans for each case. It adds
bounded weakness category labels for A02 regime-label/evidence differences and
A02 distribution/contradiction differences; A07 integrity status, finding,
quarantine, ingress, clock, and contradiction differences; plus output-shape or
other-value fallback categories. The rows retain case IDs and category labels,
but no dataset inputs, candidate outputs, field paths, or raw values. Categories are deterministic
training diagnostics from caller-supplied outputs; they are not independently
verified semantic explanations. For primary `REGIME_CORRECTNESS`, case booleans
and aggregate counts compare classified A02 regime labels by instrument and
horizon, ignoring confidence, trace, and other envelope metadata. A07
`INTEGRITY_CORRECTNESS` compares status, per-instrument findings, quarantine,
ingress counters, clock evidence, and contradictions, ignoring trace metadata.
`CORRECTNESS` continues to require full canonical output equality. Schema 1.0,
1.1, and 1.2
scorecards remain readable and keep their original canonical bytes. Case rows
must reconcile to aggregate match
counts. Primary improvement and regression bounds are compared as exact
fractions, independent of rounded display decimals. A report is always
`TRAINING_DIAGNOSTIC`,
`evaluation_verified=false`, and
`promotion_allowed=false`. The `measure` command consumes caller-supplied
unsigned output records, and a caller can misstate how they were produced; this
path does not establish an independently verified quality/reputation score or a
holdout evaluation. The separate `execute-training` command imports and runs
genome source inside the pinned offline container, but its output is also
unverified training evidence. Reports are retained as canonical, hash-addressed
scorecards in the evaluation catalog's audit chain and exported immutably to the
requested path. If a file export fails after catalog registration, retry the same
protocol, outputs, genomes, and timestamp; catalog registration is idempotent.
Filesystem publication and the SQLite commit are separate operations.

`ai-quant-evolve tournament --protocol <sha256> --period YYYY-MM --output
<path.json>` creates one immutable monthly training tournament from the
scorecards already retained for that exact protocol and month. Ranking uses the
exact match fraction and genome reference as the deterministic tie-break. The
result is hash-addressed, audit-linked, restart-verified, and pins the exact
scorecards used; later scorecards do not rewrite the published snapshot. The
tournament remains `evaluation_verified=false` and `promotion_allowed=false`.
It is a manually requested monthly snapshot, not an automatic scheduler, and
only the current correctness adapters are eligible.

`ai-quant-evolve --workspace data-local/evolution quality-profile --protocol
<protocol-sha256> --challenger-genome <genome-ref>` retains a repeatability
summary when at least two scorecards exist for that exact candidate, protocol,
and TRAINING dataset. The report pools case matches, counts improving/no-change/
guardrail-regression runs, and records cases whose challenger result varied
across runs. Its schema is `data/schemas/evolution-training-quality-profile.schema.json`.
It is an unsigned diagnostic over repeated use of the same training cases;
independent evidence and statistical generalization are not established, and
verification and promotion flags remain false.

The opt-in local scheduler publishes these snapshots for completed UTC months
when retained scorecards exist. When two or more distinct scorecards are retained
for one candidate and the same TRAINING dataset, it also publishes a bounded
repeatability profile; later evaluation times produce a new immutable profile
snapshot. It consumes at most one explicitly queued training evaluation per
cycle, running the baseline and direct child through the pinned offline runner
and retaining the resulting unsigned scorecard. Add a job with:

```powershell
ai-quant-evolve --workspace data-local/evolution queue-training `
  --protocol <protocol-sha256> --baseline-genome <baseline-ref> `
  --challenger-genome <challenger-ref>
```

The job reference is deterministic for that protocol/genome pair, so duplicate
submissions return the existing job. Job creation and state changes are retained
in a separate hash-chained local journal. A job that was `RUNNING` when its
process stopped is not replayed automatically. Inspect jobs with
`ai-quant-evolve --workspace data-local/evolution list-training-jobs`. After
verifying that every application worker for the workspace and its owned runner
container have stopped, recover the exact job revision with:

```powershell
ai-quant-evolve --workspace data-local/evolution recover-training `
  --job-ref <job-ref> --expected-updated-at <updated-at> `
  --recovery-id <unique-id> --reason "<operator reason>" `
  --confirm-worker-stopped
```

The required flag records an operator assertion; it does not inspect other
processes for you. Recovery is revision-checked, idempotent for the same
recovery ID and reason, hash-chained, and limited to three attempts per job.
Dataset admission remains restricted to TRAINING, the pinned runner rechecks
lineage and context before each execution, and both job and
scorecard authority flags remain false. The job contract is published at
`data/schemas/evolution-training-job.schema.json`. The protected status response
includes pending evaluation count, jobs completed in the last cycle, and the
latest job state.

Enable the local scheduler with
`AI_QUANT_EVOLUTION_SCHEDULER_ENABLED=1`, set
`AI_QUANT_EVOLUTION_WORKSPACE` to the catalog workspace, and configure the
operator token. It checks once per minute. Tournament and quality-profile
history resume from the audited catalog; job admission and transitions resume
from the separate job journal. The protected, no-store status route reports
pending and processed quality profiles. The worker publishes at most four
tournaments and four quality profiles and processes at most one training job
per cycle. The route is
`GET /api/v1/evolution/scheduler`. This worker runs only explicitly queued
training candidates; it does not call a model or grant promotion authority.
Hosted/HA scheduling and recovery coordination, signed promotion policy,
shadow/canary assignment and rollback remain open.

Tests cover restart/lineage, immutable version conflict, source ownership,
parent/agent/version/objective/contract/capability refusal, unchanged version-only
proposals, forged roots, concurrent idempotence, valid-chain wrong audit binding,
corrupt/deleted bodies/head, atomic audit failure rollback, literal-false admission,
planned-agent refusal, real CLI workflows and preserved exports/invalid paths.

P11 is in progress. The P11-specific isolated runner supports training
diagnostics only. A repeated-training quality profile summarizes scorecards
over the same dataset, but independent weakness validation and independently
validated quality/reputation measurement,
physical/statistical holdout isolation, independently checked candidate
replay/adversarial/shadow/canary, DSPy/generation/Jev pre-screening, full agent
coverage, independent validation of recovery operations, signed promotion policy,
production version assignment, rollback and independent review remain. The local
audited queue and opt-in single-host worker now run explicitly submitted
TRAINING jobs only.
Windows/Linux CI is configured but hosted results are unverified.
The local Linux Docker engine is now available and dependency/enforcement probes
pass. The P11 runner executes A02/A07 training contexts in separate fresh
containers, but smoke runs do not verify candidate quality, holdout behavior or
independent isolation.

`ai_quant_os.evolution_mutations` defines a closed catalogue of two source-pinned
training mutations: force available positive-return A02 regimes to `UNKNOWN`, and
force A07 status to `HEALTHY` when any instrument is quarantined. `build_mutation`
accepts only the exact server-owned 0.2.0 root genome and returns a direct 0.3.0
child with false verification/promotion flags. The pinned runner now executes each
mutation over a fault case and an unaffected control in synthetic TRAINING data.
Each baseline scores 1.0 and each challenger scores 0.5: the declared weakness is
reported on the fault case, while the control remains correct. The A02 cases cover
trending versus ranging regimes; A07 covers a stale quote versus fresh inputs with
an unverified clock. See
`docs/evaluations/evolution-mutation-corpus-0174.json`. These probes show that two
known faults are detectable and the selected controls are preserved; they do not
validate baseline quality, independent acceptance, or generalization beyond these
synthetic cases.

Manual import review found no new genome/store/CLI/source-boundary imports in the
five declared deterministic domain modules. The automatic clean-code boundary
checker could not run because `.clean/architecture.md` has prose rules without a
machine-readable `clean-architecture` block; this is not an automatic boundary
pass. The structure map was refreshed. The schema test dependency `jsonschema`
is now explicit in the development extra; the existing core A02/A07 repair-image
wheel profile has not changed and does not claim the full repository test closure.

Rollback: disable/remove `ai-quant-evolve` and preserve its separate registry and
SQLite/WAL/SHM files. Existing application databases, sealed sources, broker paths,
safety state and artifact hashes require no migration.
