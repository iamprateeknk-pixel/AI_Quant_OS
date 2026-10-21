# P07 LangGraph Runtime and 18 Agent Missions — Status

Date: 2026-10-19

## Phase verdict

P07 implementation is **integrated in local advisory/shadow mode, but not complete for promotion or release**. The repository contains the durable LangGraph runtime, typed contexts and receipts, immutable manifests, fixed tool ceilings, call admission, and server-installed 0.2.0 handlers for A01–A18. The phase remains open because every 0.2.0 mission is PARTIAL, all original 0.1.0 missions are PLANNED, and several required data sources, evaluation gates, and independent acceptance are not available.

No agent has broker submission, risk-policy mutation, credential access, production deployment, or safety-recovery authority. A completed graph receipt is advisory evidence only. Missing or stale inputs must remain WAIT_FOR_DATA/UNAVAILABLE; do not fill them with generated market facts.

## Implemented coverage

| Agent | Mission | Current implementation |
|---|---|---|
| A01 | Master Supervisor | Bounded task planning and specialist dispatch (0.2–0.4) |
| A02 | Market Regime | Deterministic multi-window shadow analysis; uncalibrated |
| A03 | Macro & Central Bank | Dated ECB-rate and event evidence; macro bias remains unassessed |
| A04 | Currency Strength | Relative currency evidence; shadow output |
| A05 | Volatility & Liquidity | Paper quote/bar and liquidity diagnostics |
| A06 | Event Risk | Event-window recommendations; synthetic/un-calibrated inputs |
| A07 | Data Integrity | Feed integrity and advisory quarantine proposals |
| A08 | Quant Research Scientist | Source-bound falsifiable hypothesis proposals |
| A09 | Strategy Builder | Guarded inert candidate construction |
| A10 | Validation Scientist | Candidate validation evidence and scorecards |
| A11 | Adversarial Validator | Rechecks retained P08 perturbation evidence |
| A12 | Strategy Selector | 0.2.0 preserves unavailable metrics; 0.3.0 reads candidate-keyed verified shadow performance while withholding rank until qualification, drift, and approval policy exist |
| A13 | Meta Allocator | 0.2.0 retains unavailable selector evidence; 0.3.0 binds one explicit current A12 0.3.0 graph result, while covariance, calibrated risk budget, and full portfolio exposure still block allocation |
| A14 | Drift & Attribution | Shadow drift diagnostics |
| A15 | Counterfactual Scientist | Read-only comparison of retained counterfactual runs |
| A16 | Health & Repair | Isolated repair readiness and candidate evidence |
| A17 | Learning & Evolution Director | Readiness report; no tournament/promotion plan without evidence |
| A18 | Incident & Safety Supervisor | Incident classification and recommendations only |

The application composition root installs all 18 handlers. Per-agent evaluations are retained under `docs/evaluations/`; detailed scope and limitations are in `docs/*_HANDOFF.md` and ADRs 0015–0019 and later linked agent decisions.

## Verified in this workspace

- 2026-10-19: The focused Windows selector/allocator/registry/graph/supervisor suite passes 144 tests. A13 0.3.0 passes an end-to-end PAPER graph test with a real A12 0.3.0 run, binds the exact retained result, and rejects absent or wrong-version upstream runs. A13 still returns no weights and no execution/promotion authority. See ADR 0207 and `docs/evaluations/meta-allocator-0207.json`.
- 2026-10-19: The context compiler now checks immutable content-addressed dataset catalogs for TRAINING/HOLDOUT bindings, verifies requested refs match the declared class before fetch, and binds catalog/ref provenance into context evidence. A08 historical evidence uses checked research receipts; A10 uses the validation package's checked holdout receipt; A11 binds its verified immutable holdout scope. A09's reviewed hypothesis is a derived summary and remains GENERAL. The focused context, budget, research, validation, builder, and adversarial suites pass 72 tests. See ADR 0208 and `docs/evaluations/agent-resource-catalog-0208.json`.
- The full Windows Python 3.11 repository suite passes 3,145 tests with 42 skipped after this increment; Ruff, format, and compile checks also pass for the changed modules.
- 2026-10-19: The research catalog now keeps TRAINING and HOLDOUT CSV bytes in separate SQLite files. Verified legacy mixed catalogs migrate holdout bytes and retain the original training-store audit chain with receipt tombstones; the new holdout store adds its own audit chain. The TRAINING reader verifies holdout metadata without selecting sealed raw bytes. Focused catalog, research intake, research generation, validation, and API tests pass 72 tests. See ADR 0209 and `docs/evaluations/research-data-isolation-0209.json`.
- 2026-10-19: Persistent PAPER startup now restores exact-version lifecycle states from an audited `.agent-lifecycle.sqlite3` sidecar before serving agent capabilities. New versions remain SHADOW, changed manifest pins or tampered journals refuse startup, and quarantine is terminal in the ordinary lifecycle journal and remains independently restored from health incidents. The focused lifecycle/registry/context/graph/health/selector suite passes **184 tests**. See ADR 0210. Durable quality observations, independent storage identity, and distributed fencing remain open.
- 2026-10-19: A17 now consumes bounded operational run counts rebuilt from audit-verified graph receipts, with explicit missing/empty states and a source fingerprint stable across restart. Completion rate is identified as an operational count only; calibration remains unavailable and A17 still withholds tournament plans and promotion. Focused graph/quality/A17/evolution tests pass **39 tests**. See ADR 0211.
- Full locked Windows Python 3.11 suite after the lifecycle journal, operational quality observations, selector worker bounds, and architecture inventory updates passes **3,156 tests, 42 skipped** in 2,711.88 seconds with `AI_QUANT_EVOLUTION_RUNNER_ENGINE_TEST=1`. Ruff and formatting checks pass for the changed modules. Operational graph-receipt completion counts remain uncalibrated; P07 acceptance gates remain open.
- `python -m compileall -q src/ai_quant_os` — passed.
- Ruff check for the registry, graph runtime, task supervisor, and agent modules — passed.
- Ruff format check for the same files — passed.

## Open gates before P07 can be called complete

1. Connect trusted, versioned source adapters for all declared market, event, macro, broker, portfolio, research, and operations inputs. Catalog checks currently cover protected context sources, but not every declared adapter.
2. Independently accept the separate TRAINING/HOLDOUT files and enforce distinct storage identities/process permissions for evaluation data. The local files still share one trusted application identity. Existing synthetic/development replays are not holdout evidence.
3. Complete each mission's declared output and evidence contract; calibrate agent confidence and quality measures on appropriate data.
4. Lifecycle transitions now persist with exact manifest pins; operational quality counts rebuild from verified durable graph receipts across restart. Quarantine remains fail-closed. Calibrated agent quality observations across restart with independently reviewed recovery semantics remain open.
5. Complete independent mission review, failure/restart/timeout/concurrency acceptance, and a verified rollback target. No mission promotion is authorized now.

Do not wire paid models, real broker execution, or live capital as a shortcut for these gates. No profit guarantee follows from the current implementation.
