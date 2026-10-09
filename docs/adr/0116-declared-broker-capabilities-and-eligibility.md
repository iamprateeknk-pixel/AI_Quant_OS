# ADR 0116: Separate declared broker interfaces from execution eligibility

Status: implemented guarded registry/skeleton slice; native acceptance pending.

P04 requires broker capability and eligibility declarations plus alternative
adapter skeletons. Adapter enabled/connected booleans alone must not confer
native execution release or silently replace a route with a duplicate identity.

Declare immutable local implementation profiles, including disabled native
execution skeletons that reject all unsupported methods. Bind copied immutable
eligibility policy to the router; allow new orders only for eligible local PAPER
simulation. Enforce a non-overridable external release guard in this slice and
require exact boolean adapter state. Preserve old order/fill lookup for safe
reconciliation after new-risk eligibility denial.

Expose the declared registry through an owner-protected no-store read. Demo reads
stay in the separately enrolled observation service; implementation capabilities
and local simulation flags do not attest account entitlement or compliance.

Consequences: future native execution needs explicit release contracts and
independent review. Skeletons are honest unsupported boundaries, not vendor API
implementations. Native acceptance, evidence-based operational eligibility,
registry UI and the full P04/P13 deliverables remain open.
