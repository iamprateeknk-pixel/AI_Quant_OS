# P05 portfolio, risk, execution, and reconciliation handoff

## Implementation

- `src/ai_quant_os/risk.py` owns the immutable risk limits, issued-decision identity, quote freshness, exposure checks, and monotonic safety states. Currency ceilings and shared exposure are implemented in `currency_risk.py` and `currency_portfolio.py`.
- `FLATTEN_ONLY` authorizes only opposite-side orders bounded by the confirmed filled position, with no pending orders. It denies new positions, reversals, ambiguous pending exposure, and all orders after escalation to `HALT`. The gate remains inside the risk decision and authorization path.
- `src/ai_quant_os/execution.py`, `order_store.py`, and `broker.py` reserve idempotency keys before dispatch, retain order state across restart, and keep broker simulator acknowledgements and fills durable. The `PAPER` router is the only order route enabled by default.
- `paper_recovery.py` and `paper_state_store.py` contain the operator-gated reconciliation path. Uncertain outcomes retain exposure and stop new risk; broker/position mismatch escalates to persistent `HALT`. Recovery requires a distinct credential and durable audit receipt.
- Agent roles have fixed tool ceilings and cannot request broker submission, policy writes, or safety recovery. Architecture checks prevent strategy/agent source from importing execution modules.

## Verification

The tests below were run with Pydantic 2.13.5 and Pydantic Core 2.46.5, matching `requirements/windows-py311.lock`:

- `tests/test_trading_path.py`, `test_currency_risk.py`, `test_currency_portfolio.py`, `test_paper_recovery.py`, and `test_paper_state_store.py`: **82 passed**. Covers idempotent retry/restart, stale quote rejection, exposure properties, uncertain broker outcomes, mismatch escalation, and persistent safety state.
- `tests/test_trading_path.py` after the flatten-state regression fix: **54 passed**. Covers bounded non-reversing reduction approval, new-risk and overshoot rejection, and authorization invalidation after escalation.
- `tests/test_api.py` after the end-to-end PAPER flatten workflow test: **27 passed**. A filled long position is reduced through accepted partial and final sell orders in `FLATTEN_ONLY`; buys and an oversize reversal are rejected, and the final fill yields a zero net position.
- `tests/test_api.py`, `test_operator_controls.py`, and `test_operator_rejections.py`: **45 passed**. Covers API reconciliation, authorization, safety transitions, and persisted receipts.
- Current combined P05 risk/recovery/API/operator/agent-capability/architecture run: **242 passed** across overlapping focused files, including the end-to-end `FLATTEN_ONLY` route and final zero-position fill.
- `tests/test_agent_registry.py` and `test_architecture_broker_boundary.py`: **112 passed**. Covers fixed agent capability ceilings and execution import boundaries.

Run the focused groups from the repository root with `uv run pytest <listed test files> -q` after syncing the Windows dependency lock. The latest combined run used all ten files above and passed 242 tests. The P05 behavior is locally tested; it is not evidence of live-broker behavior.

## Persistence and rollback

SQLite schema setup is additive and idempotent. `OrderStore` adds missing `intent_id` and `order_id` columns to legacy `paper_orders`, backfills them from retained order receipts, and adds unique indexes. Other stores create missing tables and indexes with `IF NOT EXISTS`. Back up the state directory before changing versions. Stop the local service before restoring a backup; no destructive downgrade migration is provided.

## Risks and remaining dependencies

- Broker-source fill acceptance, live account reconciliation, production currency policy, and production account limits remain unverified. The current broker evidence comes from the local `PAPER` simulator; do not treat it as live execution acceptance.
- SQLite and the local runtime assume a single workstation owner. Multi-host writers and shared-database deployment are unsupported.
- P04 native Exness demo acceptance and P13 release approval remain gates. Live execution remains disabled.
- Independent P05 acceptance review has not been recorded; the phase remains in progress.
