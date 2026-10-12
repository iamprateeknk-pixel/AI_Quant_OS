# Realtime-triggered incident monitoring — increment0157

The incident monitoring client subscribes to the existing authenticated realtime
store through an explicit public subscription/snapshot boundary. It creates no
new EventSource or browser-to-Prometheus connection and does not change the
backend safety stream, its schema, lease or capture work. Header/world consumers
continue to share the original stream. Monitoring observations are independent
protected HTTP reads triggered by that current channel, not an atomic safety/
world/monitoring snapshot or notification delivery certificate.

## Updates, authorization and privacy

While a validated stream frame is current, one shared monitoring observer reads
`/api/incidents/monitoring` at most once per15 seconds within that stream epoch.
The fixed Next GET route delegates to the existing server-only session reader,
rejects query overrides and selects only the monitoring contract before returning
no-store JSON. Browser fetches use same-origin HTTP-only cookies, no explicit
credential/header or source URL. Error responses contain generic selected text.
The existing backend checks operator authorization before and after source I/O.

The initial server read is also selected before becoming client props; foreign
fields/reasons are never serialized into the browser. Hydration transitions to
waiting/withheld until the authenticated channel and a protected observation are
current. Disabled/unavailable/expired-session data infer no rule rows. Original
UTC source timestamps, states and runbook paths remain unchanged selected fields.

## Ownership, ordering and expiry

Only one monitoring read is outstanding. Its original AbortController and
four-second caller deadline include fetch/body decoding; the existing server
reader has its own three-second bound. Browser decoded response bytes are capped
at8192. No source/control request is retried by this store. The next scheduled
observation is a new read, requiring a current original stream lease.

Each request has an internal generation. Stream disconnect/stale/withheld state,
new epoch, caller deadline and final unsubscribe invalidate it and abort only
that original work. Late completions cannot replace current state, even if their
transport ignores cancellation. Old/duplicate sequence signals are withheld.
Each subscription has its own notification wrapper, so identical callbacks can
unsubscribe independently. Last unsubscribe detaches the shared trigger,
cancels owned timers/read and removes the selected browser cache. It does not
close a stream still owned by a header/world subscriber.

Monitoring observations retain their own45-second expiry from the original source
read, irrespective of newer safety/world frames. Identical old responses cannot
renew that expiry. Original observed timestamps are compared at microsecond
precision; regressions withdraw the observation. Browser timer scheduling is
conservative at millisecond precision and never extends the source deadline.
The existing rule decoder independently validates source evaluation freshness
and120-second firing holds. A new stream epoch clears prior selected observations
and ordering reference. This is not native Prometheus process-custody attestation.

The live view shows refresh/withholding context explicitly. Automatic updates
cannot acknowledge/resolve a retained incident, restore a safety mode, retry an
order or prove delivery. If the page is not mounted there is no monitoring
observer; server engine-health detection and durable notification remain separate.

## Evidence and pending acceptance

New controlled-clock tests cover one shared read, identical callback ownership,
original expiry despite fresh frames/old responses, original microsecond clock
regression, pending-to-firing replacement, disconnect/new epoch/deadline/late
completion and cleanup. Actual existing realtime-store composition proves one
source for header plus monitoring and immediate withholding on disconnect.
Private route and browser read tests verify selection/bounds/session errors and
fixed destinations. Existing real HTTP backend SSE tests exercise safety changes
then session revocation and release original thread/listener ownership.

Evaluation0157 records the full staged regression/build/identity evidence.
Backend/runtime/requirements and112 schemas are unchanged; native wheel0155 is
retained. Prior browser launch refusal remains; no new browser or screenshot/
viewport/key/assistive-technology verification is claimed. These are polling
observations gated by SSE, not server-pushed monitoring events. Durable incident
custody/acknowledgement, external notification/engine-health delivery, production/
independent/browser and all full P00–P13 gates remain incomplete.
