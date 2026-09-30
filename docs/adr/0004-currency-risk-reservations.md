# ADR 0004 Currency risk reservations

Status: Accepted for the local paper API. Additional instruments and production account risk controls remain P05 work.

The paper API adds fixed USD reporting limits of 2,000 per currency and 4,000 gross across both currency legs. The existing 1,000 per-order and 1,500 portfolio EURUSD unit limits also apply. Instrument currency metadata belongs to the server policy. Trade payloads cannot change limits, metadata or conversion freshness rules.

The risk kernel requires a currency context when a currency policy is configured. It checks the current ledger and proposed order, including pending slippage, before issuing approval. The primary conversion quote must match the market snapshot used for that intent. Missing, expired, future-dated and untrusted rates reject valuation. A new order cannot increase a currency or gross exposure that exceeds its ceiling. Existing overages do not automatically permit an increase in another overage.

The kernel retains the immutable currency context with its issued decision. The gateway retrieves that context from the kernel; it does not accept a reservation policy from an intent. Inside `BEGIN IMMEDIATE`, SQLite compares the approval's fills and pending ledger with current records and independently recomputes the currency gate. New reservations and transitions from PENDING to DISPATCHING both perform the check. The API supplies its clock at these points, so a rate that expires during processing blocks dispatch. A checked retry of a pending order excludes its own existing reservation from the proposal and applies the gate to the remaining ledger plus that order.

Concurrent requests that observe the same ledger cannot both reserve against it. A changed ledger forces new evaluation. If prices expire after reservation, the order remains PENDING and the simulator is not called. Its original intent and idempotency key may be retried with fresh quotes. DISPATCHING and UNKNOWN outcomes still require acknowledgement reconciliation.

The low-level kernel can still be constructed without a currency policy for isolated legacy paper tests. The local API always configures the policy. No tables or serialized order fields change. Rollback preserves receipts, but older code will apply only the earlier unit gate and must not be treated as equivalent risk enforcement. Actual account funding, margin, production FX feeds, broker fees and live execution policy remain release dependencies.
