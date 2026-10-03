# Verified ECB policy-rate intake

The data command now retrieves and archives public ECB rate observations. It
supports the deposit facility (`DFR`), main refinancing fixed rate (`MRR_FR`) and
marginal lending facility (`MLFR`) series, all for EUR in percent per annum.
Series are `FM.B.U2.EUR.4F.KR.<rate-id>.LEV`. These are dated rate-change facts;
they are not a currency forecast, current-policy guarantee or order permission.

## Commands

```text
ai-quant-data fetch-ecb-rates --root data-local/ecb-rates
ai-quant-data list-rates --root data-local/ecb-rates
ai-quant-data replay-rates <rate-capture:sha256> --root data-local/ecb-rates

ai-quant-data ingest-ecb-rates <local-csv-path> --root data-local/ecb-rates --at <aware-ingestion-timestamp>
```

`fetch-ecb-rates` makes one explicit public HTTPS request to the fixed ECB API
endpoint, asking for the last two observations of each of three series. It uses
no API key, broker or paid model, and starts no worker. Local CSV import is
separately marked `LOCAL_CSV_IMPORT`; its ingestion time does not establish
historical availability. Fetch failure produces no new archive or success output.
Read commands refuse a missing archive rather than initializing one.

## Validation and provenance

The original response is capped at 65,536 bytes. Retrieval has a ten-second total
deadline by default, including streamed response reads; configurable library
deadlines must be finite and at most twenty seconds. Redirects, non-200 status,
unexpected content type and compressed responses are refused. The fixed URL and
TLS verification apply to network retrieval; ordinary configured HTTP proxy
settings are respected. Test transport injection is a library test seam.

The normalizer requires all three exact series, one or two observations each,
their fixed dimensions, normal observation status, public confidentiality code,
daily period format, percent-per-annum unit and zero unit multiplier. Duplicate
headers/dates, incomplete series and malformed/extra row cells are rejected.
Rows are ordered by rate ID and observation date. Rates can be negative, must be
finite within -100..100, have at most twenty stored digits and exponent -9..9.
No scaling, silent row truncation or substitution of missing values occurs.

`received_at` is normalized to UTC and records receipt, while `period` retains the
provider's date of rate change. Future-dated facts remain future-dated. Every
capture has `current_applicability=UNASSESSED` and
`historical_vintage_guarantee=false`. A rate unchanged for months can still have
an old change date; this interface does not infer that the latest row is the
current policy rate. Recent retrieval does not turn old periods into recent
economic observations or establish when a historical value was first published.

The strict typed contract is `schemas/rate-capture.schema.json`. Its immutable
reference hashes canonical normalized receipt bytes, which include the SHA-256
of the raw CSV, exact request URL, mode, receipt time and adapter version.
Structural source metadata validation is not an independent authentication of a
local CSV. Library publishers are trusted local callers; they can claim a capture
mode and receipt time. Only the explicit HTTP adapter establishes the tested
retrieval path.

## Archive and replay

The new `rates.sqlite3` stores raw CSV, canonical capture JSON and its publication
event in one transaction. Capacity is at most 128 captures, each with 65,536 raw
bytes and 16,384 normalized bytes. Exact duplicate receipt publication is
idempotent; newer receipts get distinct references even with unchanged raw data.
Receipt order cannot move backwards. There is no automatic rotation/deletion.

Every list/get/raw read checks capacity, journal integrity, publication coverage,
canonical bytes and identity, and reconstructs normalization from the raw CSV.
Existing empty databases or missing metadata are refused; no old journal is
re-baselined. Actual SQLite concurrent connections commit duplicate publication
once. Locks/errors produce a nonzero command result without printing success.
Read verification uses a local immediate transaction and may contend with writers.

The hash chain is local, with no independent external anchor. Entire archive
deletion/replacement or privileged complete resealing is not independently
detectable. Crash atomicity relies on SQLite and host storage; power-loss,
distributed/object-store publication and production retention remain unverified.

## Actual retained source capture

`evaluations/ecb-policy-rate-capture.json` retains a successful actual public HTTPS
capture from 2026-10-03T11:04:39.926859Z. The original 2,421-byte response is
`evaluations/ecb-policy-rates.csv`. All three series contain two dated observations;
the capture reference and raw bytes replay from the local archive. JSON Schema
validation passed. Four named implementation hashes bind the normalizer, HTTP
adapter, archive and command module. The dispatcher, transitive runtime and
environment are outside these named pins. LF attributes preserve source pins;
the raw CSV is retained without newline conversion.

The official [API documentation](https://data.ecb.europa.eu/help/api/data) and
[data examples](https://data.ecb.europa.eu/help/api/data-examples) describe the
endpoint and last-observation query. The [deposit-facility series metadata](https://data.ecb.europa.eu/data/datasets/FM/FM.B.U2.EUR.4F.KR.DFR.LEV)
identifies its dated-change semantics and percent-per-annum unit.

This completes a bounded local ECB intake path in P02/P06. A03 remains planned
until these facts are connected to its checked macro/event/rates/world inputs
and driver outputs. Other currencies, yields, releases and central-bank text,
point-in-time historical availability, continuous scheduling, calibration,
independent review and all promotion/live gates remain open. No application
agent, risk policy or existing snapshot is changed by this data increment.

## Rollback

Preserve the rate archive, raw response, schema and receipt/source bytes before
reverting the increment. No shared database migration is introduced. The older
data command lacks these rate subcommands; use a compatible reader for retained
captures. The existing local paper app does not require a restart.

## Local verification

The full suite passed 1,209 tests with 15 skips in 855.19 seconds, with actual
built-frontend integration required. The 57 focused importer/provider/archive/
command checks passed in 3.37 seconds. Lint and formatting passed for 250 Python
files. Both 96-package locks reproduced and installed dependency consistency
passed. Actual network capture and installed-command restart replay matched;
raw/capture/schema/source pins verified in the worktree and fresh index checkout.
All retained A04/A05/A06 reports, fixtures, source/evaluator hashes and schemas
passed preservation checks. Four sealed strategy closures, ten retained artifact/
schema checks, nine older artifact bytes and earlier genome/protocol checks
passed. Staged changes and all 82 previous commits passed secret scanning. The
structure map was refreshed. Five deterministic domain modules are unchanged;
manual import and adapter-boundary review passed. Automatic architecture checking
remains unavailable without its declaration. Hosted/Linux runtime, power-loss,
production and independent review remain open.
