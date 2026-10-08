# 0094: Read verified health incidents through the operator session

Date: 2026-10-09

Use the existing server-owned session for protected health reads, keeping credentials
out of component data. Bound responses independently from the smaller credential
body limit. Compose transport and the health feature in the app layer; the feature
owns unknown-JSON validation and presentation.

Add a metadata-only paged endpoint alongside the original health list/detail routes.
Verify the full incident journal in one read transaction before sorting and slicing.
Bind pages to the original head; require it for continued reads and reject changed
heads rather than silently mixing observations. Export a frozen typed response schema.
Do not expose raw captured evidence, auto-scan, acknowledge, recover or remove pins.

Present disabled monitoring and restored quarantine separately. Show observed scan
freshness without inferring health, promotion or current safety from historical records.
Use a single health status observation instead of combining separate worker reads.
Full incident resolution, trace correlation, realtime delivery and independent release
acceptance remain separate required development.
