# ADR 0114: Compose demo sync as an explicit foreground service

Status: implemented and fixture verified; native acceptance pending.

The durable controller needs an executable timer and observable lifecycle.
Compose its existing worker/gateway/projection dependencies behind a foreground
package entry point. Require an explicit launch flag and enrolled Windows demo
connection for enabled operation. Receive the password from one environment key,
exclude it from the child environment and retain the private worker stdin path.

Publish bounded public heartbeat snapshots by atomic file replacement. Readers
mark expiry and unavailable data explicitly. Stop events prevent future ticks;
current bounded capture completes under existing worker custody rules. Do not
reset journals, adopt orphan children or install/restart a service automatically.

Consequences: the public heartbeat is local unverified observation, not native
connectivity or ownership proof. OS service supervision, owner-protected HTTP/UI
composition and explicit halted recovery remain future work. Native demo and
all live release gates remain pending; orders/portfolio mutation stay disabled.
