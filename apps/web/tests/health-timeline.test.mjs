import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
const path = new URL('../src/features/health/timeline.ts', import.meta.url);
const source = readFileSync(path, 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { parseHealth, parseTimeline } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const incident = { schema_version: '1.0', incident_id: 'a'.repeat(64), code: 'AGENT_FAILED', subject: 'original-run', detected_at: '2026-10-09T00:00:00Z', agent_id: 'A02', agent_version: '0.1.0', manifest_ref: `agent-manifest:${'b'.repeat(64)}`, safety_state: 'NO_NEW_RISK', quarantine: true, status: 'OPEN', transitions: [], promotion_allowed: false };
const page = { schema_version: '1.0', mode: 'PAPER', head_hash: 'c'.repeat(64), total: 1, offset: 0, limit: 25, next_offset: null, incidents: [incident] };
test('disabled monitoring has no fabricated health count or scan timestamp', () => {
  const health = parseHealth({ schema_version: '1.0', enabled: false, state: 'DISABLED' });
  assert.equal(health.state, 'DISABLED');
  assert.equal(health.open_incidents, null);
  assert.equal(health.last_tick, null);
});
test('restored quarantine remains visible while monitoring is disabled', () => {
  const health = parseHealth({ schema_version: '1.0', mode: 'PAPER', enabled: false, state: 'RESTORED_ONLY', open_incidents: 1, quarantined_versions: ['A02@0.1.0'], last_tick: null, last_error: null, promotion_allowed: false });
  assert.deepEqual(health.quarantined_versions, ['A02@0.1.0']);
});
test('timeline projects safe metadata and rejects inconsistent policy or page identities', () => {
  assert.deepEqual(parseTimeline({ ...page, incidents: [{ ...incident, evidence_json: '{"secret":"must-not-render"}' }] }).incidents, [incident]);
  for (const payload of [{ ...page, mode: 'LIVE' }, { ...page, total: 0 }, { ...page, next_offset: 25 }, { ...page, incidents: [incident, incident] }, { ...page, incidents: [{ ...incident, safety_state: 'NORMAL' }] }, { ...page, incidents: [{ ...incident, promotion_allowed: true }] }, { ...page, incidents: [{ ...incident, detected_at: 'invalid' }] }]) assert.equal(parseTimeline(payload), null);
});
test('unknown monitor states and future policy vocabulary are withheld', () => {
  assert.equal(parseHealth({ schema_version: '1.0', enabled: true, state: 'HEALTHY' }), null);
  assert.equal(parseTimeline({ ...page, incidents: [{ ...incident, code: 'UNKNOWN' }] }), null);
  assert.equal(parseHealth({ schema_version: '1.0', enabled: false, state: 'DISABLED', mode: 'LIVE' }), null);
});
test('quote lifecycle history is shown only when its close and reopen sequence is valid', () => {
  const closed = { action: 'CLOSED', at: '2026-10-09T00:01:00Z', command_id: 'operator-close', incident_id: incident.incident_id, reason: 'trusted quote restored', transition_id: `${incident.incident_id}:1` };
  const quote = { ...incident, code: 'QUOTE_STALE', agent_id: null, agent_version: null, manifest_ref: null, safety_state: 'NO_NEW_RISK', quarantine: false, status: 'CLOSED', transitions: [closed] };
  assert.equal(parseTimeline({ ...page, incidents: [quote] }).incidents[0].status, 'CLOSED');
  assert.equal(parseTimeline({ ...page, incidents: [{ ...quote, code: 'AGENT_FAILED' }] }), null);
  assert.equal(parseTimeline({ ...page, incidents: [{ ...quote, status: 'OPEN' }] }), null);
  assert.equal(parseTimeline({ ...page, incidents: [{ ...quote, transitions: [{ ...closed, transition_id: 'wrong' }] }] }), null);
});
