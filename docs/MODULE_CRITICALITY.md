# Module criticality classification

## Classification boundary

The machine-readable classification lives in `.clean/architecture.json` under `module_criticality`. It partitions every Python module in `package_module_inventory` into exactly one group. `tests/test_architecture_broker_boundary.py::test_every_package_module_has_an_explicit_criticality_classification` enforces a complete, non-overlapping partition and checks representative authority and supporting modules.

**Critical** means a module directly enforces or composes local paper order authority, risk and market validation, operator/session controls, broker admission, agent admission, or durable order/safety/audit state. Critical changes require focused failure-path tests and independent review before phase promotion.

**Supporting** means a module is not itself a deterministic authority owner in that boundary. Supporting code can still alter inputs, research conclusions, evidence, privacy, availability, or operator presentation. The label is not a claim that the module is low-risk, correct, or exempt from security review. A change that moves authority into a supporting module requires reclassification and an ownership update.

## Critical module inventory

The exact source-path list is maintained once in `.clean/architecture.json`; the current set covers API composition, agent admission and run custody, audit chain, broker eligibility, domain contracts, execution, graph runtime, market data, operator controls and sessions, order storage, paper instruments/market ingress/recovery/state, portfolio, and risk. This list describes local paper authority only. It does not identify a production/live release owner, which remains unimplemented.

## Supporting modules and agent boundary

All other inventoried package modules are explicitly in the supporting set. Agent and strategy implementations may produce proposals and evidence but cannot own risk or broker authority. The import guard in `tests/test_architecture_broker_boundary.py` blocks direct imports from broker/execution modules across discovered agent and strategy sources. Static import scanning does not prove the entire indirect runtime call graph; module criticality does not replace that guard or a full security review.

## Change procedure

When adding or moving a Python package module:

1. Add it to `package_module_inventory`.
2. Assign it to exactly one criticality group using the definitions above.
3. If it owns or composes a paper control, update `CONTROL_OWNERSHIP.md` and define its dependency boundary.
4. Add focused tests for authority, failure, and persistence behavior before treating the classification as accepted.
5. Obtain independent critical-path review before any phase promotion.

This classification closes the inventory/criticality bookkeeping gap only. It does not close P00 architecture layering, full transitive dependency constraints, threat modeling, independent review, or any P01-P13 gate.