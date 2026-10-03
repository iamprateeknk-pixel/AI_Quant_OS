# Historical research data catalog

This is the historical input boundary for the planned A08 research mission.
It retains operator-supplied bar CSV and immutable TRAINING/HOLDOUT labels in a
separate audited SQLite catalog. A08 remains PLANNED; this addition does not
generate a hypothesis, run a strategy, score a holdout or authorize promotion.

## Register and inspect data

Use the installed command with an explicit catalog and CSV path:

```powershell
ai-quant-research-data --catalog data-local/research-data.sqlite3 register history.csv --source-id declared-development --resource-class TRAINING
ai-quant-research-data --catalog data-local/research-data.sqlite3 register holdout.csv --source-id declared-holdout --resource-class HOLDOUT
ai-quant-research-data --catalog data-local/research-data.sqlite3 list
```

The API's catalog is stored beside the configured paper database, named
`paper.research-data.sqlite3`. With operator authentication, submit
`POST /api/v1/paper/research/datasets` with `source_id`, `resource_class` and
`csv`. Inspect classified receipt metadata through
`GET /api/v1/research/datasets`. There is no HTTP raw-data read, relabel or
deletion route. Without an operator token, both routes return 503; missing or
incorrect credentials return 401 when an operator token is configured.

CSV must have exactly these columns and seven nonempty fields per row:
`instrument_id,observed_at,open,high,low,close,volume`. Registration requires
2–4,096 unique rows for one supported paper instrument, valid OHLC/volume and
aware timestamps. Parsed observations are normalized to UTC. Future observations
relative to the receipt clock, duplicates, extra fields and unknown instruments
are refused. The raw input is bounded at 1 MiB, the catalog at 64 records and
8 MiB of total raw CSV. No input is executed as code.

Receipts retain raw SHA-256, the existing canonical dataset fingerprint derived
under fixed decimal precision, row count, instrument and first/last observation.
Identical source/raw/class publication returns its first receipt without changing
its receipt time. An opposite classification cannot share any inclusive time
range for the same instrument, even when prices or source labels differ. Checks
and publication occur in one SQLite transaction, including concurrent writers.

## Agent data access

The trusted `training` reader requires current TRAINING_READ capability and
SANDBOX scope before and after its complete verified read. It returns TRAINING
receipts and newly reconstructed bar datasets; it never returns HOLDOUT rows or
their metadata to the agent. Current quarantine and permission revocation deny
the read. Planned versions can possess read capabilities without gaining an
executable implementation. The operator metadata view is a separate human path.

SANDBOX here is an application permission scope. This catalog does not provide
OS isolation or authorize candidate/verifier execution. Raw holdout and training
bytes share one trusted-process database, whose integrity verification reads all
records internally. Physical separation, per-read durable audit, statistical
independence, data-provider authenticity and isolated validation remain open.
Receipts explicitly report market authenticity UNVERIFIED and independence
UNASSESSED. Historical receipt time supplies no claim of current market freshness.
Experiment-specific causal partitioning remains the existing ResearchPlan gate;
catalog labels alone do not prove an experiment valid.

## Integrity and verification

Raw bytes, canonical receipt and receipt-bound audit event commit atomically.
Every read/write checks bounded complete catalog contents and audit binding.
Missing metadata, altered raw data, damaged receipts/journal and an existing
zero-byte database refuse operation. No damaged journal is silently baselined.
The local chain lacks an external anchor and does not prevent complete hostile
rewriting of the database and chain together.

Tests cover operator API restart, label conflicts, concurrent registration,
current capability/scope denial, revocation during read, capacity, corruption,
extra CSV fields and caller decimal precision. The actual installed command was
also exercised against two declared synthetic CSV files and a fresh-process list.
Its receipts are development intake evidence, not market or strategy evaluation.
See BUILD_STATUS and ADR 0063 for the final regression record.

## Rollback

Stop the owned application and preserve the whole state root, including this
catalog, before reverting. Retain original source CSV/receipt bytes. Older code
cannot serve the new routes; reverting must not remove holdout labels or reuse
classified observations under a different classification.
