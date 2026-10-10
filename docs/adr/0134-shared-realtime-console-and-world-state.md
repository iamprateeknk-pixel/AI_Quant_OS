# ADR 0134: Own console observation in one shared subscription store

## Decision

Use a read-only public realtime snapshot hook for the persistent header,
Mission Control and World State. Centralize connection ownership, cache identity,
original-lease expiry, reconnect backoff and closed-channel callback rejection.
Use React's external-store subscription interface with an empty deterministic
server snapshot. Keep feature workflows and command acknowledgements independent.

## Projection

Retain full reported world features and currency coverage through the safe web
projection. Validate bounded original numeric strings and currency/fresh-count
coherence; display original values without rounding. Preserve source validity
and show retained observations as historical when current state is unavailable.

## Limits

The existing backend schema and control authority are unchanged. Real browser
acceptance, production distribution/load, other views' detail subscriptions and
independent critical review remain pending. See SHARED_REALTIME_WORLD_HANDOFF.md
and machine evaluation 0134. No full-phase or live-capital completion is claimed.
