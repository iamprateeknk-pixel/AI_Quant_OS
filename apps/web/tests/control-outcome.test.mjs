import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const source = readFileSync(new URL('../src/features/operator-session/control-outcome.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const api = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const id = '35caf51c-5d20-4bc7-9bb1-e694a206df0e';
const pending = { schema_version: '1.0', mode: 'PAPER', receipt_id: id, trace_id: id, action_id: id,
  operator_session_id: id, request_sha256: 'a'.repeat(64), server_timestamp: '2026-10-09T00:00:00Z',
  decision: 'PENDING', previous_state: 'NORMAL', authoritative_state: 'NORMAL',
  policy_checks: ['PAPER_ONLY','VALID_SESSION','FRESH_REAUTH','RESERVED_NOT_APPLIED'] };
test('pending reservation remains distinct from terminal outcomes', () => {
  assert.equal(api.parseOutcome(pending, id).decision, 'PENDING');
});
test('a pending receipt cannot claim an applied state or a different action identity', () => {
  assert.throws(() => api.parseOutcome({ ...pending, authoritative_state: 'HALT' }, id));
  assert.throws(() => api.parseOutcome(pending, 'different'));
  assert.throws(() => api.parseOutcome({ ...pending, policy_checks: [] }, id));
});
