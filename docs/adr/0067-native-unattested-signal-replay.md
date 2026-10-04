# 0067: Replay bounded numeric targets separately from proposed source execution

Date: 2026-10-04
Status: Accepted

Retained A09 source cannot execute on the parent host. A future admitted worker
can produce bounded per-open target values while the trusted parent performs
native trading replay. Implement the native numeric adapter now, keeping its
inputs explicitly operator supplied/unattested and every source/execution/
promotion claim disabled.

Bind a complete target tape to its retained package and holdout, including exact
open timestamps and previous-two-closed-bar fingerprints with fresh holdout
warmup. Use actual Nautilus 1.221.0 market fills and percentage taker commissions
in quote currency. Apply the declared synthetic half-spread/slippage price model
and stress multiplier. Independently reconstruct fills/cash flows and reject
substituted but arithmetically consistent reports.

Nautilus margin mode bypasses balance checks; add explicit conservative full
target ask-notional/quote-equity admission before opening/reversing exposure.
Keep fixed synthetic depth, currency basis, final marking, financing/conversion
and liquidation limits explicit. Native realized P&L is an unchecked cache field.
Expose a bounded local JSON command with exclusive output; no HTTP/agent endpoint.

Use a separate adapter because the earlier native runners and their immutable
strategy source closures retain fixed-fee behavior. Do not rewrite those sealed
modules or register an unattested tape as a source-verified strategy. Actual
candidate isolation and validation remain prerequisite work.

See SIGNAL_TAPE_REPLAY_HANDOFF.md and BUILD_STATUS.md for usage and evidence.
