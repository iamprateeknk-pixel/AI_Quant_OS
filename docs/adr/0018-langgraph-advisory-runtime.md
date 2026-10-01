# ADR 0018: Local LangGraph advisory execution

## Decision

Use LangGraph 1.2.12 with SQLite checkpoint support 3.1.1 for the initial advisory supervisor. The graph has admission, execution and finalization nodes. Its only state is the run identifier. Evidence, exact manifest, source references, expiry and results live in a separate application-owned invocation ledger in the same agent SQLite file.

The server installs trusted Python implementations against exact manifest hashes and output schema names. An implementation receives a compiled context and a task-bound tool interface. Every tool call checks current context and permissions before invocation and before returning its output. The runtime accepts only the installed typed output schema, bounds canonical output to 64 KiB, and records typed failure states without exception details. Manifest and evidence deadlines bound cooperative execution. All receipts require escalation and have no order authority.

The shipped catalog's 18 mission implementations remain PLANNED. The application installs no mission handlers. Missing inputs yield WAIT_FOR_DATA; a complete context for an absent handler yields UNAVAILABLE. Test handlers demonstrate runtime behavior and are not production mission implementations.

## Persistence and replay

Each run identity is immutably bound to a full context/manifest checkpoint. Creation, the pre-call RUNNING reservation, and each terminal result commit atomically with their audit events. Reads verify canonical hashes, schema/provenance and the exact audit binding. The ledger permits at most 10,000 runs and 128 KiB per record; new creation fails at capacity and existing identical retries remain readable.

LangGraph checkpoints use synchronous durability and strict deserialization with empty module allowlists and no pickle fallback. Checkpoint values must match the requested thread identifier. Checkpoints cannot replace the ledger's evidence, permissions or invocation reservation. Environment-enabled LangSmith tracing is explicitly disabled during graph execution.

An interrupted graph that has not reserved an invocation may resume, checking current permissions and evidence again. A RUNNING invocation may already have contacted a provider or tool. Resume turns it into UNCERTAIN, withholds output and never repeats the call. Terminal retries return the stored receipt without another agent call. A read endpoint never resumes or changes state. Cross-process competing callers can conservatively produce a transition conflict or UNCERTAIN; this is not a distributed lease scheduler.

Receipts retain original created_at, valid_until and input_refs. Cached completion remains historical evidence after expiry or quarantine. The API reports context_current separately; that flag describes the context time window and permissions, not market fitness or trading approval.

## API and startup

With AI_QUANT_PAPER_DB configured, application lifespan opens a sibling file named `<paper stem>.agents.sqlite3`. Without persistent configuration, runtime commands return 503. All execution, resume and archive endpoints require the operator token:

- POST `/api/v1/paper/agents/execute`: run_id and an existing context_ref; clients cannot submit graph state, tools or evidence.
- GET `/api/v1/agents/graph-runs/{run_id}`: verified archival receipt.
- POST `/api/v1/paper/agents/graph-runs/{run_id}/resume`: continue an uninvoked checkpoint, or retain a terminal/uncertain outcome.

The SDK supports pausing before execution for recovery verification. HTTP clients cannot choose graph breakpoints or mutate checkpoints. The paper order database, risk engine and broker remain outside agent interfaces.

## Limits and remaining work

SQLite is a local checkpoint backend. Trusted Python handlers are not an OS sandbox. Cancellation/timeouts are cooperative; storage lock waits can outlast an evidence window. Local hashes and audit chaining do not resist an attacker rewriting the entire database and integrity metadata. Registry lifecycle still resets to SHADOW on restart. Temporal workers, distributed leases, lifecycle persistence, monetary governance, sandbox isolation, model routing, quality metrics, all mission graphs and release gates remain unfinished.

Rollback must preserve both the paper and agent SQLite files. The graph increment adds tables and dependencies; reverting code must not erase uncertain invocation reservations or audit evidence.

## Sources

[LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence), [graph API](https://docs.langchain.com/oss/python/langgraph/graph-api), [LangGraph package](https://pypi.org/project/langgraph/), and [SQLite checkpoint security and usage](https://pypi.org/project/langgraph-checkpoint-sqlite/). Versions and installed interfaces were checked on 2026-10-01.
