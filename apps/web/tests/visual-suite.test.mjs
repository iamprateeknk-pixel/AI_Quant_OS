import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdir, mkdtemp, rm, stat } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import sharp from 'sharp';
import { compareVisualSuite, MISSION_CONTROL_VISUAL_STATES } from './visual-suite.mjs';

async function makeCapture(file, color, width = 4, height = 3) {
  await sharp({ create: { width, height, channels: 4, background: color } }).png().toFile(file);
}

test('six-state visual suite compares every required state and writes each diff', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'ai-quant-visual-suite-'));
  const references = path.join(root, 'reference');
  const captures = path.join(root, 'current');
  const diffs = path.join(root, 'diff');
  await mkdir(references);
  await mkdir(captures);
  try {
    for (const name of MISSION_CONTROL_VISUAL_STATES) {
      await makeCapture(path.join(references, name), '#123456');
      await makeCapture(path.join(captures, name), '#123456');
    }
    const result = await compareVisualSuite(references, captures, diffs);
    assert.equal(result.status, 'passed');
    assert.equal(result.states.length, 6);
    assert(result.states.every((item) => item.changedPixels === 0));
    for (const name of MISSION_CONTROL_VISUAL_STATES) assert((await stat(path.join(diffs, name))).size > 0);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test('missing reference and capture states fail the visual suite', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'ai-quant-visual-suite-'));
  const references = path.join(root, 'reference');
  const captures = path.join(root, 'current');
  await mkdir(references);
  await mkdir(captures);
  try {
    const result = await compareVisualSuite(references, captures, path.join(root, 'diff'));
    assert.equal(result.status, 'failed');
    assert.equal(result.states.length, 6);
    assert(result.states.every((item) => item.status === 'missing_reference_and_capture'));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test('pixel drift over the suite budget fails only the affected state', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'ai-quant-visual-suite-'));
  const references = path.join(root, 'reference');
  const captures = path.join(root, 'current');
  await mkdir(references);
  await mkdir(captures);
  try {
    for (const name of MISSION_CONTROL_VISUAL_STATES) {
      await makeCapture(path.join(references, name), '#000000');
      await makeCapture(path.join(captures, name), name === MISSION_CONTROL_VISUAL_STATES[0] ? '#ffffff' : '#000000');
    }
    const result = await compareVisualSuite(references, captures, path.join(root, 'diff'), { maxDiff: 0.5 });
    assert.equal(result.status, 'failed');
    assert.equal(result.states.filter((item) => item.status === 'failed').length, 1);
    assert.equal(result.states.find((item) => item.status === 'failed').name, MISSION_CONTROL_VISUAL_STATES[0]);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test('suite CLI exits unsuccessfully when the six-state capture set is incomplete', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'ai-quant-visual-suite-'));
  const references = path.join(root, 'reference');
  const captures = path.join(root, 'current');
  await mkdir(references);
  await mkdir(captures);
  try {
    const result = spawnSync(process.execPath, [
      path.resolve('scripts/compare-visual-suite.mjs'),
      '--reference-dir', references,
      '--current-dir', captures,
      '--diff-dir', path.join(root, 'diff'),
    ], { encoding: 'utf8' });
    assert.equal(result.status, 1);
    assert.match(result.stdout, /"status":"failed"/);
    assert.equal((result.stdout.match(/missing_reference_and_capture/g) ?? []).length, 6);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
