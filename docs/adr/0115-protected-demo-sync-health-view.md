# ADR 0115: Read demo service health through the existing owner boundary

Status: implemented API/UI slice; browser and native acceptance pending.

Operators need visibility into the foreground demo sync service without placing
native SDK work or enrollment credentials in the web process. Read one bounded
heartbeat file from a server-owned directory through the existing operator
session/token boundary. Label its source as local and unverified; missing or old
records must remain unavailable or stale.

Compose a fixed no-store server read into Brokers. Project validated public
metadata and original times, with explicit sign-in, halted and stale states.
Expose pending references as diagnostic observations, with no mutation controls.
The local launcher assigns the fixed backend directory but never starts the
broker service or forwards its password.

Consequences: this is snapshot monitoring, not a native connectivity, ownership
or history completeness proof. Browser interaction acceptance remains pending
because the prior launch was blocked by policy. Native demo and live release
acceptance, service deployment and operator recovery remain open.
