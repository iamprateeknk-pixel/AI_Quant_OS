import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const source = readFileSync(new URL('../src/features/operator-session/pending-action.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const api = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const command = { action_id: '35caf51c-5d20-4bc7-9bb1-e694a206df0e', requested_action: 'HALT', reason: 'Paper halt',
  operator_session_id: '46caf51c-5d20-4bc7-9bb1-e694a206df0e', client_timestamp: '2026-10-09T00:00:00Z', expected_current_state: 'NO_NEW_RISK', confirmation: 'HALT' };

test('restored pending command preserves its original identity and timestamp', () => {
  assert.deepEqual(api.decodePending(api.encodePending(command)), command);
});
test('browser checkpoint rejects credential fields, invalid identities and altered confirmation', () => {
  for (const input of [{ ...command, credential: 'synthetic-secret' }, { ...command, session_token: 'private' }, { ...command, action_id: 'invalid' }, { ...command, confirmation: '' }]) {
    assert.throws(() => api.encodePending(input));
  }
  assert.throws(() => api.decodePending('x'.repeat(4097)));
  assert.throws(() => api.decodePending('{bad'));
});
test('storage failure is observable before any caller can send its command', () => {
  const storage = { getItem: () => null, setItem() { throw new Error('Quota exceeded'); } };
  assert.throws(() => api.savePending(storage, command), /Quota exceeded/);
});
test('checkpoint can be removed only by matching terminal receipt identity', () => {
  const values = new Map();
  const storage = { setItem: (key,value) => values.set(key,value), getItem: (key) => values.get(key) ?? null, removeItem: (key) => values.delete(key) };
  api.savePending(storage, command);
  assert.throws(() => api.clearPending(storage, 'wrong'));
  assert.equal(values.size, 1);
  api.clearPending(storage, command.action_id);
  assert.equal(values.size, 0);
});

test('an unresolved action cannot be replaced by another command', () => {
  const values = new Map();
  const storage = { setItem: (key,value) => values.set(key,value), getItem: (key) => values.get(key) ?? null };
  api.savePending(storage, command);
  assert.throws(() => api.savePending(storage, { ...command, reason: 'Altered original command' }));
  assert.deepEqual(api.decodePending([...values.values()][0]), command);
});
