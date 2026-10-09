# ADR 0111: Keep demo sync observations distinct from portfolio accounting

Status: accepted for local groundwork; native and order-lifecycle acceptance pending.

P04 requires account, position, order and fill synchronization. Reuse the fixed
read worker and durable observation journal for a declared ACCOUNT_SYNC profile.
Keep the original quote contract unchanged and publish a separate sync receipt
schema. Bind history windows to attempt identity and preserve custody blocking
across both profiles.

Normalize account and individual position/order facts. Separate trading deals
from cash/correction events and preserve signed broker monetary fields. Do not
reinterpret broker lots, cash records or commissions as paper portfolio fills.
Report the sequential-read consistency limit, unknown account-cache freshness
and current-symbol basis for historical unit conversion explicitly.

Withhold whole batches on unsupported symbols, malformed/duplicate records,
oversize output, changed account, stale quotes or failed cleanup. Do not silently
truncate, guess missing native values, infer atomicity or refresh a reused attempt.
Full native acceptance, correction/cursor processing, continuous reconnection,
external order idempotency and release authorization remain separate requirements.
