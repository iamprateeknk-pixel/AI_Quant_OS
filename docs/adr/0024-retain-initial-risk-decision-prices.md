# ADR 0024: Retain initial Risk Kernel decision prices

Status: Accepted for paper execution evidence; P09 and live release gates remain open.

Capture the approval's actual frozen market snapshot from the Risk Kernel. Persist its quote, approval/time/identity binding and checksum with the order reservation before dispatch. Preserve that initial benchmark across acknowledgement, idempotent retries and pending recovery. Make absence distinguishable from corruption with a required-record marker.

Migrate old orders to explicitly unknown benchmarks. Do not infer an initial price from later quotes or recovery decisions. Read ownership, fills and benchmarks atomically for reporting.

Decompose actual fill cash into midpoint reference, initial spread, decision-to-fill difference and fees. Group by original strategy/version and instrument, reconcile every measured sum to actual cash and keep quote currencies separate. Refuse inexact arithmetic and withhold components with incomplete historical coverage.

The measurement is initial-decision implementation shortfall. It cannot assign market movement, latency or broker causality without additional retained inputs. The protected read API does not grant risk, candidate promotion or broker authority.
