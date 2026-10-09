# ADR 0129: Display protected saved demo observations

## Decision

Compose a server-rendered demo observation panel into the existing Portfolio screen. Read the fixed owner-protected endpoint through the established server-only session boundary. Preserve paper portfolio authority and keep all demo order/capital controls absent.

Project and validate selected financial strings, source references and bounded position/order rows before rendering. Keep decimal strings intact, reject unsafe native integer identities and unsupported numeric representations, and derive expiry from the original microsecond source lease. Missing observations do not imply zero balances or native readiness.

Label all financial values as recorded; stale values remain inspectable with original timestamps. Display the page-fetch time and use an explicit native document reload to request another authenticated snapshot. No browser storage, background polling, credential forwarding to client components or inferred portfolio totals is added.

## Consequences

Operators can inspect the saved demo account snapshot without affecting paper execution or risk. Extremely large identities and unsupported decimals are unavailable instead of silently losing precision. A static page can outlive its source lease, so its fetch-time label and reload action remain explicit. Browser acceptance and native broker/full-history accounting are still separate unmet gates.
