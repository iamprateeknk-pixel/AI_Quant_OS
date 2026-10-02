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
```

Replace placeholders with actual retained references/paths. The module form is
`python -m ai_quant_os.evolution_cli`. Seed accepts an objective JSON containing:
schema version 1.0, a supported metric, its fixed MAXIMIZE/MINIMIZE direction,
finite nonnegative minimum improvement and a 64-character lowercase protocol SHA.
Metric names are correctness, calibration error, evidence age, reliability,
latency, tool-error rate, USD cost and regime correctness in their explicit
uppercase wire forms. Improvement for rate metrics is bounded to one.

The legacy `--objective` path retains a caller-owned opaque predeclaration.
`--protocol` instead derives the exact objective from a verified retained same-agent
plan in the evaluation catalog. Neither path proves actual evaluation or grants
release authority. Model and feature configuration is descriptive and never dispatched.
See `EVOLUTION_PROTOCOL_HANDOFF.md` for the new dataset/plan commands and limits.

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
  import-policy certificate. Only owned source is retained, not a full runtime
  package/dependency closure. Future execution still needs the complete isolated
  worker, trusted verifier and independent certificates.
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

Tests cover restart/lineage, immutable version conflict, source ownership,
parent/agent/version/objective/contract/capability refusal, unchanged version-only
proposals, forged roots, concurrent idempotence, valid-chain wrong audit binding,
corrupt/deleted bodies/head, atomic audit failure rollback, literal-false admission,
planned-agent refusal, real CLI workflows and preserved exports/invalid paths.

P11 is in progress. Quality/reputation and weakness measurement, executable protocol
adapters, physical/statistical holdout isolation, actual candidate eval/replay/adversarial/shadow/canary,
DSPy/generation/Jev pre-screening, full agent coverage, scheduler, monthly tournament,
promotion policy, production version assignment, rollback and independent review
remain. Windows/Linux CI is configured but hosted results are unverified.

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
