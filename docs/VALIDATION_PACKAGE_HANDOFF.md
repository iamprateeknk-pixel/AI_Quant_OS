# Validation input package

This prerequisite connects the retained A09 candidate to exact source, historical
observations and a declared plan. It does not run a strategy or establish an A10
validation mission. The package reports `WAIT_FOR_ISOLATED_VALIDATOR`, with tests
and validation `NOT_RUN`; execution, isolation and promotion flags remain false.

## Prepare and inspect

With an operator token, publish a strictly later same-instrument `HOLDOUT` dataset
through the existing classified dataset route. Then call:

`POST /api/v1/sandbox/validation/packages`

```json
{
  "run_id": "your-completed-A09-run",
  "holdout_ref": "research-data:<64-character-digest>",
  "plan": {
    "plan_id": "your-immutable-plan-id",
    "rationale": "Synthetic development validation preparation.",
    "spread_bps": "2",
    "slippage_bps": "1",
    "commission_bps": "0.5",
    "cost_stress_multiplier": "2",
    "walk_forward_train_bars": 8,
    "walk_forward_test_bars": 2,
    "monte_carlo_trials": 1000,
    "random_seed": 27
  }
}
```

These sample costs are invented development inputs. The plan explicitly labels
them `OPERATOR_DECLARED_SYNTHETIC`; financing and account currency conversion are
unavailable. Planned out-of-sample, walk-forward, Monte Carlo, cost stress and
reality-gap methods are declarations. No method is executed by this endpoint.
This input plan is not a complete acceptance protocol or a profitability claim.

Both publication and `GET /api/v1/sandbox/validation/packages/{package_ref}`
return metadata only. Source text, raw training/holdout CSV and original context
payloads stay in the local archive. Missing operator identity is denied; a
tokenless preview refuses these operations. Client scope/execution/authority
fields are forbidden. No agent context, tool or raw HTTP reader is installed.

## Retained identity

The separate sibling `paper.validation-packages.sqlite3` archive retains:

- The original checked completed A09 0.2.0 graph and its exact candidate recipe,
  review, genome, proposed source and tests.
- Exact CSV bytes and immutable classification receipts. Training must match the
  original hypothesis's dataset reference, fingerprint, instrument and end time.
  Holdout must be distinct, labelled HOLDOUT, the same instrument, and start
  strictly after the original training ends. Declared windows must fit.
- Observed local bytes for `ai_quant_os/__init__.py`, `contracts.py`,
  `market_data.py` and `strategy_contracts.py`. The latter two must match the
  original builder pins. A static import inventory rejects unexpected direct
  SDK imports. Proposed source and tests are never parsed, compiled or imported.
- The declared plan and UTC preparation timestamp, bound by package/request
  hashes and an atomic local audit event.

Byte snapshots use canonical base64 to preserve source line endings and raw CSV.
Every read and write checks the bounded archive, nested identities and exact
audit binding. Missing metadata, zero-byte existing databases, deleted rows and
changed bytes are refused without reinitialization. SQLite serializes concurrent
publication. An identical request returns its original receipt, including the
original timestamp. A plan ID cannot change contents; a different plan needs a
different ID. Archive limits are 32 packages, 4 MiB per package and 32 MiB total;
each dataset remains limited to 1 MiB and 4,096 observations.

Preparation reads original archival artifacts. The earlier review grants inert
candidate construction only; it does not grant validation, execution or
promotion. Retaining validation inputs is a separate operator action and does
not renew that review or claim current A09 context authority. Completed archival
graphs may have expired contexts. Future execution must have its own admission.

## Limits and next stage

Four observed SDK files cover the fixed profile's local static import inventory.
They are not an independently attested baseline, runtime/pytest dependency
closure, validation harness, execution adapter or OS confinement certificate.
The package contains proposed code as inert JSON and never materializes it as
executable files. Python/pytest wheels, isolated worker admission, compilation,
proposed tests, runner integration and actual validation remain open.

The operator supplied both datasets and selected the holdout. The shared local
catalog/package archive does not prove statistical independence, blindness,
market authenticity or physical holdout isolation. Multiple separate store
reads are checked individually and are not a cross-store atomic snapshot.
Source files are observed individually; local audit has no external anchor.
Broker-measured costs, financing, quote/account currency conversion and a full
acceptance protocol must be supplied before any promotion gate can pass.

The registry remains 28 manifests with ten partial missions. A10 0.1.0 remains
planned; no placeholder scorecard or A10 implementation is registered here.
All production and live release gates remain open.

Before rollback, preserve the full state root including the new validation
package archive, original strategy-build, classified data and graph journals.
Older runtimes cannot validate or execute these retained candidates.
