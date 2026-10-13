# Forward shadow counterfactual replay

The protected PAPER endpoint runs declared alternatives against the exact forward bars in a retained candidate shadow report. It reruns the original shadow configuration first and refuses a completed report unless that baseline reproduces exactly. Alternatives must use the same registered strategy implementation and parameter names as the assigned candidate; each may declare a different supported configuration, quantity, and cost set.

## API

`POST /api/v1/paper/shadows/{shadow_id}/counterfactual` requires the operator token. The request supplies a stable `replay_id`, the retained shadow `sequence` and `report_sha256`, and one to seven alternatives. It does not accept source bars. The server loads the report from the verified shadow archive and retains both the source report and the completed counterfactual output in `shadow-counterfactual.sqlite3`.

Repeating the same replay ID and exact request returns the archived report. Reusing the ID with changed inputs, or encountering an unfinished reservation, returns a conflict and requires operator investigation. Invalid source history or archive integrity returns a service error. The API does not route through a broker, alter candidate lifecycle, grant promotion authority, or claim causal attribution.

The request and response schemas are `docs/schemas/shadow-counterfactual-input-v1.schema.json` and `docs/schemas/shadow-counterfactual-output-v1.schema.json`.
