# Shared current console state and World State (0134)

## Delivered

The persistent console header, Mission Control operations panel and World State
view now subscribe to one private realtime store per browser module instance.
The header no longer retains a one-time safety read across navigation. It shows
current safety only from an unexpired accepted frame; disconnected, expired or
withheld state is explicitly unknown. World State retains its regime, feature
and currency-coverage tables, with automatically updated paper observations.

The public `useRealtime` hook is the read-only shared snapshot interface for
feature views. View components do not own credentials, create extra connections,
change the snapshot or invoke controls. This is a shared application observation
boundary; individual feature workflows and command receipts remain separate.

## Subscription ownership

`useSyncExternalStore` supplies a stable cached client snapshot and a fixed empty
server snapshot. Server rendering never opens an event source or invents initial
data. The first active subscriber owns one connection; further subscribers reuse
it. The last unsubscribe closes it, detaches callbacks and clears expiry/retry
timers. The persistent header keeps this connection available through navigation.
Different browser tabs remain separate subscribers at the backend.

An error closes the old channel and retries with bounded exponential delay from
two to thirty seconds. This includes failed access before sign-in: the next
connection uses the current private server cookie, so a fatal EventSource HTTP
response does not permanently strand the view. Successful accepted data resets
the delay. Callbacks from an old/closed connection cannot republish its state.
The original frame expiry owns the stale timer; resubscription, receipt or retry
never renews the old observation. A rejected sequence or epoch retains previous
evidence as historical, with current state withheld.

All original 0133 backend bounds, per-frame authorization, full-state resets and
safe command acknowledgements remain unchanged. No persistent delta replay,
cross-worker sequence or broker authority is added.

## World projection

The existing backend already supplies full world-state features. The selected
web projection now retains mid price, spread, return and volatility as exact
original strings, with nulls preserved as unavailable. It checks bounded finite
decimal syntax, sign constraints, overflow and underflow before forwarding;
the table performs no financial arithmetic or numeric rounding.

Coverage validates every observed currency against the instrument set, unique
ordered related-pair inventories and the exact number of FRESH quote statuses.
The macro flag is the original per-snapshot observation, not inferred feed
availability. Extra fields are stripped at the proxy. The existing full backend
snapshot schema and original fixture remain unchanged; no old schema is rewritten.

Disconnected/expired/withheld world data is visibly labelled as the last accepted
observation. Its original quote status and coverage are historical, and are not
relabelled as current. An empty current snapshot remains explicit about missing
paper quotes. Actual market/broker source custody and strategy calibration are
not inferred from this simulated view.

## Evidence and outstanding acceptance

Tests exercise connection sharing, original expiry, stable snapshot identities,
SSR emptiness, access retry, old callbacks, sequence/epoch rejection, cleanup and
historical presentation. A separate populated quote/bar fixture is produced by
the actual protected backend. Its exact response is checked except boot epoch;
the same original feature values and currency counts survive proxy and client
validation. Evaluation 0134 records staged-file identity and fresh checks.

Native browser navigation, login/retry timing, timer throttling, visibility,
keyboard/focus and visual acceptance remain pending under the existing browser
launch refusal. Per-tab/multi-worker production load, all fifteen views' detail
subscriptions, order/portfolio/cost/agent updates, OTel/Grafana and independent
critical review remain open. All P00–P13 remain incomplete. No provider, broker
SDK, remote push, promotion or live capital is activated.

## Rollback

Revert this increment to restore the page-specific panel connection and static
header/world reads. There are no database or backend schema migrations. Preserve
both original and populated fixture/evaluation artifacts. Backend 0133 reads and
authority checks are unchanged.
