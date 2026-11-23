import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const compile = (source, jsx = false) => `data:text/javascript;base64,${Buffer.from(
  ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.ESNext,
      target: ts.ScriptTarget.ES2022,
      jsx: jsx ? ts.JsxEmit.ReactJSX : undefined,
    },
  }).outputText.replace(
    '"react/jsx-runtime"',
    JSON.stringify(pathToFileURL(require.resolve('react/jsx-runtime')).href),
  ),
).toString('base64')}`;

const parserSource = readFileSync(
  new URL('../src/features/agents/operational-quality.ts', import.meta.url),
  'utf8',
);
const panelSource = readFileSync(
  new URL('../src/features/agents/operational-quality-panel.tsx', import.meta.url),
  'utf8',
);
const { parseAgentOperationalQuality } = await import(compile(parserSource));
const { AgentOperationalQualityPanel } = await import(compile(panelSource, true));

const payload = () => ({
  schema_version: '1.0',
  as_of: '2026-11-23T12:00:00Z',
  availability: 'AVAILABLE',
  reason: null,
  source_receipt_count: 1,
  source_receipts_sha256: 'a'.repeat(64),
  source_trace_count: 1,
  source_traces_sha256: 'b'.repeat(64),
  rows: [{
    agent_id: 'A02',
    agent_version: '0.2.0',
    manifest_ref: `agent-manifest:${'c'.repeat(64)}`,
    run_count: 1,
    status_counts: {
      CREATED: 0,
      RUNNING: 0,
      COMPLETED: 1,
      UNAVAILABLE: 0,
      EXPIRED: 0,
      DENIED: 0,
      WAIT_FOR_DATA: 0,
      INVALID: 0,
      FAILED: 0,
      TIMEOUT: 0,
      UNCERTAIN: 0,
    },
    completion_rate: '1',
    timing_sample_count: 1,
    mean_duration_seconds: '1E-7',
    p95_duration_seconds: '1e-7',
  }],
  metric_scope: 'OPERATIONAL_OUTCOME_COUNTS',
  calibration_established: false,
  promotion_allowed: false,
});

const render = (result) => renderToStaticMarkup(
  React.createElement(AgentOperationalQualityPanel, { result }),
);

test('accepts exponent-form duration decimals from the Python API', () => {
  const parsed = parseAgentOperationalQuality(payload());
  assert.equal(parsed?.rows[0].mean_duration_seconds, '1E-7');
  assert.equal(parsed?.rows[0].p95_duration_seconds, '1e-7');
});

test('renders verified outcomes while keeping quality and promotion claims unavailable', () => {
  const value = parseAgentOperationalQuality(payload());
  assert(value);
  const html = render({ ok: true, value });
  assert.match(html, /Archived operational outcomes/);
  assert.match(html, /A02 · 0\.2\.0/);
  assert.match(html, /1E-7 s \/ 1e-7 s · 1 samples/);
  assert.match(html, /not calibrated model quality, an SLA, current health, or promotion eligibility/);
  assert.match(html, /Calibration established: no\. Promotion allowed: no\./);
});

test('withholds rows when the server cannot verify the graph archive', () => {
  const html = render({ ok: false, reason: 'UNAVAILABLE' });
  assert.match(html, /The graph archive could not be verified/);
  assert.doesNotMatch(html, /<tbody>|A02|1E-7/);
});
