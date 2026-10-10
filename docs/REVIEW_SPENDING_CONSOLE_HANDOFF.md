# Protected reviewer spending observations in Costs (0136)

## Delivered

Costs now reads configured OpenAI review spending through the private server
session, alongside the existing routine-call ledger. Reads run in parallel;
their separate journal heads and accounting meanings are preserved. This is a
saved point-in-time observation, not a realtime subscription or combined ledger.
The page explicitly gives the backend observation time and a native GET refresh
form, requesting a new server snapshot without a spending mutation.

The new protected GET /api/v1/providers/review-spending reuses existing operator
authorization before and after the ledger read. Revocation during a read withholds
the result. Successful reads are no-store. No provider request, reservation,
settlement, reset or recovery action is performed by this endpoint.

The projection verifies original 0135 custody in one read transaction, retains
all eighteen role counters and adds declared ceilings and unsettled capacity.
ENFORCED and BREACHED observations must have complete metadata, coherent UTC
periods, all unique roles, consistent reserves/remaining limits and matching hold
counts. Unavailable/unconfigured observations cannot invent rows or totals.
BREACHED retains verified original counters and clearly blocks generation.

## Exact display and meaning

The token table shows daily/monthly committed capacity, declared limits, remaining
routine capacity, protected reserves and unsettled requests/held tokens. The USD
table shows equivalent amounts at configured rate ceilings. Money crosses the
API boundary as bounded canonical integer nanodollar strings and formats through
BigInt to exact nine-decimal USD. No Number conversion or rounding is applied.
Native breached amounts above JavaScript integer precision are covered separately.

Committed capacity includes both reported usage and unresolved full reservations.
Unsettled does not mean proven failed: a request may still be running or have
ended without a verified report. Unknown holds carry to later periods until
settled. This view is not a provider invoice and does not claim every model route,
compute or data cost is measured. Protected reserves remain unavailable for routine
review and recovery spending stays disabled.

The frontend selects only typed observation fields, strips extra/private fields,
validates ceilings/periods/provenance and never renders rejected rows. The backend
withholds unavailable/invalid custody. The server read uses a fixed protected path,
HttpOnly session, existing loopback-origin guard, no redirects/cache and bounded
131,072-byte responses. No credential or ledger path is sent to the component.
No browser-to-provider or spending-control route is added.

## Evidence and acceptance

An actual protected backend read with an original pending reservation supplies the
new frontend fixture; comparison ignores only the random journal head from the
creation budget ID. Tests cover authentication, no-store, read-only original head,
unconfigured/corrupt observations, retained breach, integer precision, coherent
counter failures, session revocation during read, fixed private server access,
selected parsing and real React server rendering of table/empty/breach states.
The additive observation schema is separate from every original schema.
Evaluation 0136 records fresh staged identity, focused regressions, all frontend
Node tests, build and lint evidence.

Native browser navigation, refresh, focus, table overflow, accessibility and visual
acceptance remain pending under the earlier launch refusal; no alternate browser
attempt is made. Realtime cost details, shared multi-provider/model governance,
Jev budgets, actual provider/price/invoice acceptance, recovery spending, retention,
throughput and independent critical review remain open. All P00–P13 remain incomplete.

## Rollback

Revert 0136 to remove the protected observation endpoint and spending panels. Keep
the 0135 ledger/pin and new fixtures/evidence. Existing call/order/world schemas
and databases are unchanged; there is no data migration or quota reset. Review
spending admission continues independently of its Cost Center presentation.
