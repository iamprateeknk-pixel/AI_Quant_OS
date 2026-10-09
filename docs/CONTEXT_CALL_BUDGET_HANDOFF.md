# Context fetch call admission

Declared context source fetches now use the same durable quota as graph tool calls.
The API injects one call budget into the general paper compiler and A08 research,
A09 builder and A10 validation compilers. Supervisor child contexts use that paper
compiler. Direct development evaluators retain their unconfigured behavior.

After source and dataset-class permission checks, each bound input reserves one
routine call before its fetch. The reservation binds agent/version, read tool,
scope, task and a canonical hash of the input name and resource class. Evidence
contents and task text are not retained in the quota database. Each declared input
is an attempt, including multiple inputs backed by the same read tool.

Permission is checked again after asynchronous admission. Quota I/O runs outside
the event loop and inside the compiler's existing overall deadline. Cancellation
or deadline expiry while admission is running prevents fetching; an already-running
reservation thread can still commit its charge. Failed, timed-out, untrusted and
subsequently denied fetches retain reservations. Missing, unauthorized and denied
holdout inputs are rejected before charging. Concurrent inputs share routine
capacity and cannot spend protected recovery reserves.

Admission failures produce existing `DENIED` context inputs with no payload or
evidence references. Contexts remain degraded. A10 requires its complete original
validation evidence, so denied fetches now report the existing HTTP 403 admission
error before comparison; damaged archives continue to report integrity failure.
No context schemas, original quota policy or historic reservation records change.

## Verification

Missing injection and actual zero-quota API checks failed before implementation.
An actual A10 test exposed exhausted capacity being labeled an integrity error;
the admission check was added at composition before original-summary comparison.
Tests cover shared graph/context quota across versions and restart, denied holdout,
failed/timed-out/untrusted evidence, concurrent inputs, revocation during admission,
caller cancellation and overall deadlines, task/input hash binding, retained
corruption without configuration, all four API compiler paths and emergency paper
controls. Exact staged evidence is recorded in
`evaluations/context-call-budgets-0100.json`.

## Remaining scope

These are logical declared fetch attempts. Snapshot preparation and immutable
archive verification before compiler construction remain outside this admission;
this does not claim every physical database/file read is blocked before quota.
Standalone callers must inject the budget. Legacy Jev/OpenAI calls, provider token
and monetary admission/settlement, recovery spending, storage retention/migration,
fleet deployment and independent acceptance remain pending. Browser acceptance of
the updated Costs wording remains pending after the previous rejected preview
launch. No paid provider, broker or proposed candidate program was executed.
P00-P13 remains incomplete.
