# ADR 0112: Retain account observations with source-bound deal revisions

Status: implemented bounded library slice; independent broker acceptance pending.

Account sync returns sequential observations for a declared history window.
Repeated windows must not duplicate deals, erase revisions or turn missing
records into inferred execution events.

Keep a separate SQLite projection journal bound to verified finished gateway
receipts. Audit and projection writes share a transaction. Pin account and
connection scope; require increasing capture timestamps and connected history
intervals. Retain changed normalized deal content as revisions, and flag absence
without deleting history. Compare source commitments on replay, even after quote
expiry. A read-only source loader never dispatches missing work.

Consequences: bounded replay costs grow with retained receipts. Capacity fails
closed; archive/checkpoint migration is future work. Historical views remain
observations, with explicit stale quote context and unverified account freshness.
They grant no order or portfolio authority and do not certify native correction,
settlement, history completeness or atomic capture. Native acceptance, lifecycle
reconciliation and all live release gates remain open.
