# Protected retained demo portfolio observations — increment 0128

The foreground demo sync CLI publishes portfolio.json beside health.json. It
contains a bounded observation of the latest verified projection's account,
positions and pending orders, with original source attempt/receipt, account scope,
observation/expiry timestamps and retained history coverage. It grants no order,
portfolio mutation, native connectivity or acceptance authority.

## Service publication

The CLI passes the fixed state-directory portfolio path to run_service. After
each completed controller tick, the service reads the existing audited projection
and publishes an OBSERVED, EMPTY or WITHHELD frame using its current service run
UUID. OBSERVED retains account balance/credit/equity/profit/margin/free margin,
position lots/units/prices/profit/swap/identities and normalized pending orders.
Configuration, login/server/password, terminal path, personal native comments,
SDK details, raw historical deals and quote payloads are excluded. These are
private account amounts and remain behind the existing owner boundary.

Quantity basis remains CURRENT_SYMBOL_METADATA, account/history/native
attestation UNVERIFIED, and consistency NON_ATOMIC_SEQUENTIAL_READ. No historical
unit conversion, instrument/currency aggregate valuation, execution-fill import
or cash posting inference is added. The frame describes saved observations and
does not establish current account consistency or broker availability.

Source expiry is copied unchanged from the batch's earliest quote lease. The
record validates a positive source lease of at most three seconds and UTC source/
history timestamps. Republishing during a later timer tick does not extend that
expiry. Future publication/source clocks are withheld. Records reject mixed
position/account currencies and duplicate position ticket/identifier or order
ticket identities. Position/order inventories retain the existing eight-row
bounds without truncation. Declared empty/withheld frames contain no account data.

The shared mt5_publication module now owns bounded unique-field JSON reading and
atomic record writes for both heartbeat and portfolio. Each file is capped at
16 KiB; the writer flushes/fsyncs an owned temporary file and replaces its final
file, removing only that temporary file on ordinary failure. Failed replacement
preserves the previous complete record. Directory power-loss durability and host
filesystem integrity are not attested. The files are separate publications, not
an atomic pair; matching run IDs aid correlation without proving simultaneous
reads.

Portfolio publication/source failure stops further ticks and publishes service
HALTED/SERVICE_FAILURE when heartbeat storage is available. A controller capture
and projection may already have committed; this does not roll back their audited
state or invent a controller halt. Offline inspection still reports actual state.
An earlier portfolio file may survive failed storage until its original source
expiry. Empty disabled service runs publish EMPTY without native work. Library
run_service calls retain their previous behavior when the optional portfolio path
is omitted. One-shot inspection/recovery/comparison/checkpoint commands still
publish no portfolio or heartbeat.

## Owner-protected API

GET /api/v1/portfolio/demo-observation reads only fixed portfolio.json beneath
the existing absolute server-configured demo-sync state directory. It uses the
same operator token/session guard as protected heartbeat reads. Unauthorized and
revoked sessions are refused before filesystem reading; client path/query values
do not select a source. Responses use Cache-Control: no-store and include server
configuration presence, source_kind LOCAL_UNVERIFIED_DEMO_PORTFOLIO and fixed false
authority fields. POST is unsupported.

The reader returns AVAILABLE only for an observed frame before its original
source expiry, STALE after expiry, and UNAVAILABLE for empty/withheld, missing,
oversized, duplicate-field, malformed or future input. Historical stale frames
retain their source data and timestamps. AVAILABLE means local file/source-window
availability, not native health or permission to trade. No private diagnostic text
or arbitrary filesystem path is returned on failure. The existing paper positions,
currency/risk state and execution router are unchanged; demo observations never
enter their authoritative inputs.

The versioned publication/API schemas are demo-portfolio-publication.schema.json
and demo-portfolio-api.schema.json. Existing wire schemas and journal formats
remain unchanged. The backend launcher already supplies the fixed state directory
without forwarding a broker password to backend or frontend. Frontend rendering
of this new observation remains pending; this increment connects service data to
the protected portfolio API.

## Verification and rollback

Checks cover actual normalized account/position/order data and source expiry,
republication after expiry, empty/closed stores, future/malformed/bounded JSON,
authority and lease denial, mixed currency/duplicate identities, atomic replacement
failure, stop after publisher failure with retained healthy controller, exact run
correlation, owner/revoked-session protection, fixed source/no-store response,
unconfigured/missing data, unchanged paper portfolio, and real disabled CLI without
SDK/password. Existing heartbeat checks passed before and after I/O extraction.
No actual SDK, terminal, credentials, broker or live capital is exercised.

Rollback preserves all journals and prior records; older code ignores portfolio.json.
Stop service launchers before replacing code. Remove the added read/publication
path without deleting custody state. Native completeness/account freshness,
currency aggregate/risk integration, cash/cancellation acceptance, frontend/browser,
capacity rollover, installed services, independent review and P13 gates remain
pending. Focused checks do not establish full-system acceptance.
