import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import ts from 'typescript';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const require = createRequire(import.meta.url);
const compile = (source) => ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const parser = `data:text/javascript;base64,${Buffer.from(compile(
  readFileSync(new URL('../src/features/health/timeline.ts', import.meta.url), 'utf8'),
)).toString('base64')}`;
globalThis.healthLink = ({ children, ...props }) => React.createElement('a', props, children);
const source = readFileSync(new URL('../src/features/health/health-panel.tsx', import.meta.url), 'utf8')
  .replace('import Link from "next/link";', 'const Link=globalThis.healthLink;')
  .replace('from "./timeline"', `from "${parser}"`);
const { HealthPanel } = await import(`data:text/javascript;base64,${Buffer.from(compile(source)
  .replace('"react/jsx-runtime"', JSON.stringify(pathToFileURL(require.resolve('react/jsx-runtime')).href)))
  .toString('base64')}`);
delete globalThis.healthLink;

const id = 'a'.repeat(64);
const incident = {
  schema_version: '1.0', incident_id: id, code: 'QUOTE_STALE', subject: 'EURUSD',
  detected_at: '2026-10-09T00:00:00Z', agent_id: null, agent_version: null,
  manifest_ref: null, safety_state: 'NO_NEW_RISK', quarantine: false, status: 'CLOSED',
  transitions: [{ action: 'CLOSED', at: '2026-10-09T00:01:00Z', command_id: 'close-1',
    incident_id: id, reason: 'trusted quote restored', transition_id: `${id}:1` }],
  promotion_allowed: false,
};
const status = { schema_version: '1.0', mode: 'PAPER', enabled: false, state: 'RESTORED_ONLY',
  open_incidents: 0, quarantined_versions: [], last_tick: null, last_error: null,
  promotion_allowed: false };
const timeline = { schema_version: '1.0', mode: 'PAPER', head_hash: 'c'.repeat(64), total: 1,
  offset: 0, limit: 25, next_offset: null, incidents: [incident] };

test('closed quote lifecycle renders its audited reason and keeps recovery distinct', () => {
  const html = renderToStaticMarkup(React.createElement(HealthPanel, {
    statusResult: { ok: true, value: status },
    timelineResult: { ok: true, value: timeline },
    observedAt: '2026-10-09T00:02:00Z',
  }));
  assert.match(html, /CLOSED/);
  assert.match(html, /trusted quote restored/);
  assert.match(html, /Closure does not lower risk state or remove agent quarantine/);
});
