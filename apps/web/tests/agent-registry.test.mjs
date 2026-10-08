import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const path = new URL('../src/features/agents/registry.ts', import.meta.url);
const source = readFileSync(path, 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { parseRegistry } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const agent = { schema_version: '1.0', agent_id: 'A02', agent_version: '0.2.0', name: 'Regime analyst', squad: 'Market', mission: 'Classify paper regimes', output_type: 'RegimeEvidence', required_inputs: ['world_state'], allowed_tools: ['read_world_state'], implementation_status: 'PARTIAL', timeout_seconds: 2, manifest_ref: `agent-manifest:${'a'.repeat(64)}`, lifecycle: 'SHADOW' };
const wire = { schema_version: '1.0', mode: 'PAPER', agents: [agent] };

test('retains distinct registered versions and backend status without invented readiness', () => {
  const result = parseRegistry({ ...wire, agents: [agent, { ...agent, agent_version: '0.1.0', implementation_status: 'PLANNED' }] });
  assert.equal(result.length, 2);
  assert.equal(result[0].implementation_status, 'PARTIAL');
  assert.equal(result[0].lifecycle, 'SHADOW');
});
test('rejects malformed, duplicate, unbounded and non-paper registry data', () => {
  for (const payload of [ { ...wire, mode: 'LIVE' }, { ...wire, agents: [agent, agent] }, { ...wire, agents: [{ ...agent, lifecycle: 'READY' }] }, { ...wire, agents: [{ ...agent, timeout_seconds: Infinity }] }, { ...wire, agents: [{ ...agent, manifest_ref: 'unverified' }] }, { ...wire, agents: [{ ...agent, required_inputs: [] }] }, { ...wire, agents: Array(1025).fill(agent) } ]) assert.equal(parseRegistry(payload), null);
});
test('projects only public manifest fields and accepts an honest empty registry', () => {
  assert.deepEqual(parseRegistry({ ...wire, agents: [] }), []);
  assert.deepEqual(parseRegistry({ ...wire, agents: [{ ...agent, operator_token: 'untrusted-extra' }] }), [agent]);
});
