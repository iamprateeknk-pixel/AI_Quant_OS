# ADR 0163: Announce navigation search result changes

Date: 2026-10-12

## Decision

The destination search dialog exposes its result count and no-match state through a polite live region. The dialog has a programmatic name, and its query field has an explicit label. This makes search feedback available to screen readers while users type.

## Limits

Server-rendered markup verifies the dialog name, label and initial announcement. Browser screen-reader behavior and the full frontend accessibility scan remain unverified; all remaining P00-P13 gates remain open.
