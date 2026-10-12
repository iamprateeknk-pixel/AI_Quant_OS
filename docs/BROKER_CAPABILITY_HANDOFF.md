# Broker capabilities, eligibility and native skeletons — increment 0116

## Declared implementations

The immutable local registry lists five explicit profiles:

| Profile | Implementation | Declared operations |
| --- | --- | --- |
| PAPER | Paper simulator | Place/lookup simulated orders, read/report simulated fills |
| EXNESS_MT5_DEMO | Demo observation library | Quote, account, position, active-order and deal reads |
| EXNESS | Disabled execution skeleton | None |
| IBKR | Disabled execution skeleton | None |
| ZERODHA | Disabled execution skeleton | None |

Operations describe implemented local interfaces, not vendor entitlements,
native connectivity, accepted deployment or account eligibility. The demo profile
remains separate from order routing; its actual service observation stays in the
existing protected heartbeat view. Unknown native symbols/capabilities are not
invented. Only mapped PAPER instruments appear in this registry.

`IBKRAdapterSkeleton`, `ZerodhaAdapterSkeleton` and `ExnessExecutionSkeleton`
implement the existing broker protocol shape with disabled/disconnected state.
They import no vendor SDK or credentials. Every place/lookup/fills/all-fills method
raises an explicit unsupported error. Empty fills or acknowledgments are never
fabricated as successful native responses. Skeletons are registered as declared
implementation profiles, not installed into the active paper execution router.

## Enforcement

`BrokerRouter` accepts immutable `BrokerEligibility` declarations copied into its
private binding. Eligibility requires a strict boolean and one of
LOCAL_SIMULATION_ONLY, UNVERIFIED or DENIED. The existing default PAPER composition
uses eligible local simulation; absent explicit entries in a supplied mapping
are denied. Unknown binding keys or non-policy values are rejected.

New dispatch checks enabled/connected flags as exact booleans, rejects every
non-PAPER execution route regardless of adapter flags, then requires local paper
eligibility before symbol/type/expiry validation and placement. Duplicate broker
IDs are rejected rather than silently replacing a registered adapter. Public
connection/enablement observations also reject non-boolean flag coercion.

LOCAL_SIMULATION_ONLY is a technical scope for the local simulator. It is not a
legal clearance, jurisdiction decision, account entitlement or live release
approval. Setting it on an external adapter cannot enable execution. There is no
native release override, compliance mutation endpoint or environment toggle.
Future external execution still requires the P13 release contract and independent
review. The simulator remains SDK-free even if someone gives it a native label.

Read-only lookup/fill reconciliation remains available for already retained paper
orders after eligibility is denied; new risk is blocked without erasing receipts.
Direct simulator placement also rejects a new order when disabled/disconnected,
while replay of an already stored matching acknowledgment remains available.
This preserves existing idempotency/reconciliation behavior and serialized
paper order/fill contracts.

## Protected read model

GET `/api/v1/brokers/capabilities` uses the normal operator/session boundary and
no-store response. Its versioned schema reports declared operations/module,
implementation state, actual binding to the execution router, eligibility scope,
local broker admission subset and explicit false live release flags. It does not
create adapters, initialize an SDK, connect an account or alter portfolio state.
The existing `/api/v1/brokers` shape and default PAPER values stay compatible.
Risk ceilings, safety state, command expiry, symbol/type and other execution
checks still apply after local broker admission.

## Broker health read contract

Protected GET `/api/v1/brokers/health` exposes the versioned PAPER-only
`BrokerHealthRegistry` v1.0 contract. It lists exactly the five declared broker
profiles and reads only the current `BrokerRouter` connection snapshot. A missing
adapter is `UNKNOWN`; the existing paper adapter can report its declared
connection state. Quote age, order latency, reject rate, reconciliation, spread,
and account availability are not currently measured by this router, so the
endpoint returns null measurements and `UNASSESSED` component states. It never
calculates a health score from missing evidence. `updated_at` identifies the
observation time, not the freshness of any broker telemetry.

The JSON Schema is published at `docs/schemas/broker-health.schema.json` and
checked against the runtime model. The endpoint requires operator authorization
and disables caching. `live_execution_released` is fixed false at registry and
row level; this read contract cannot authorize a broker or enable trading. Adding
real health measurements requires source-specific semantics, freshness limits,
error handling, and independent acceptance before any score is considered.
## Verification and remaining acceptance

Tests reproduce the prior enabled-external routing gap and duplicate identity
overwrite before fixes. Checks cover strict eligibility/scope, native release
denial despite local flags, unsupported skeleton methods, non-boolean state
refusal, immutable caller policy binding, retained order lookup, protected registry,
exact schema and false release assertions. Existing simulator, full paper trading,
API, recovery and execution attribution checks are included in verification.

No native SDK, terminal, credentials, actual broker or live capital was used.
Registry dashboard rendering, operational eligibility evidence, native connector
execution/order lifecycle/idempotency, complete sync/history reconciliation,
operator/orphan recovery, independent review and all P13 gates remain pending.
The three native execution profiles remain unsupported. This increment does not
complete P04 or declare native broker/compliance acceptance.

## Rollback

No journal layout, order/fill schema or dependency lock changes are introduced.
Retain all existing paper/native observation journals and custody evidence.
Removing the eligibility/release guard would restore the tested routing gap and
must not be used as a way to enable external execution.
