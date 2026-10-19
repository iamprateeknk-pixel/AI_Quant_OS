import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm, stat } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import sharp from 'sharp';
import { compareVisualImages } from './visual-diff.mjs';

async function makeImage(file, color, width = 4, height = 3) {
  await sharp({ create: { width, height, channels: 4, background: color } }).png().toFile(file);
}

test('identical captures report zero pixel drift', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'ai-quant-visual-'));
  try {
    const reference = path.join(root, 'reference.png');
    const current = path.join(root, 'current.png');
    await makeImage(reference, '#336699');
    await makeImage(current, '#336699');

    assert.deepEqual(await compareVisualImages(reference, current), {
      width: 4,
      height: 3,
      comparedPixels: 12,
      changedPixels: 0,
      changedRatio: 0,
      maxChannelDelta: 0,
    });
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test('comparison applies pixel tolerance, masks dynamic regions, and writes a diff image', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'ai-quant-visual-'));
  try {
    const reference = path.join(root, 'reference.png');
    const current = path.join(root, 'current.png');
    const diff = path.join(root, 'diff.png');
    await makeImage(reference, '#000000');
    await makeImage(current, '#000000');
    const changed = Buffer.alloc(4 * 3 * 4);
    changed.fill(0);
    changed[(0 * 4 + 0) * 4] = 8;
    changed[(0 * 4 + 0) * 4 + 3] = 255;
    changed[(1 * 4 + 3) * 4] = 255;
    changed[(1 * 4 + 3) * 4 + 3] = 255;
    const currentImage = await sharp({ create: { width: 4, height: 3, channels: 4, background: '#000000' } }).raw().toBuffer();
    for (let i = 0; i < currentImage.length; i += 4) currentImage[i + 3] = 255;
    currentImage.set(changed.subarray((0 * 4 + 0) * 4, (0 * 4 + 0) * 4 + 4), (0 * 4 + 0) * 4);
    currentImage.set(changed.subarray((1 * 4 + 3) * 4, (1 * 4 + 3) * 4 + 4), (1 * 4 + 3) * 4);
    await sharp(currentImage, { raw: { width: 4, height: 3, channels: 4 } }).png().toFile(current);

    const result = await compareVisualImages(reference, current, {
      pixelThreshold: 10,
      maskRects: [{ x: 3, y: 1, width: 1, height: 1 }],
      diffPath: diff,
    });
    assert.equal(result.comparedPixels, 11);
    assert.equal(result.changedPixels, 0);
    assert.equal(result.changedRatio, 0);
    assert.equal(await stat(diff).then((value) => value.size > 0), true);
    assert.equal((await sharp(await readFile(diff)).metadata()).width, 4);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test('dimension mismatch is rejected with both image sizes', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'ai-quant-visual-'));
  try {
    const reference = path.join(root, 'reference.png');
    const current = path.join(root, 'current.png');
    await makeImage(reference, '#ffffff', 4, 3);
    await makeImage(current, '#ffffff', 3, 3);
    await assert.rejects(compareVisualImages(reference, current), /dimensions differ: reference 4x3, current 3x3/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test('a mask that excludes every pixel is rejected rather than treated as a pass', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'ai-quant-visual-'));
  try {
    const reference = path.join(root, 'reference.png');
    const current = path.join(root, 'current.png');
    await makeImage(reference, '#000000', 4, 3);
    await makeImage(current, '#ffffff', 4, 3);
    await assert.rejects(compareVisualImages(reference, current, {
      maskRects: [{ x: 0, y: 0, width: 4, height: 3 }],
    }), /masks exclude every image pixel/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test('CLI exits unsuccessfully when changed-pixel ratio exceeds its budget', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'ai-quant-visual-'));
  try {
    const reference = path.join(root, 'reference.png');
    const current = path.join(root, 'current.png');
    const diff = path.join(root, 'diff.png');
    await makeImage(reference, '#000000');
    await makeImage(current, '#ffffff');
    const result = spawnSync(process.execPath, [
      path.resolve('scripts/compare-visual.mjs'),
      '--reference', reference,
      '--current', current,
      '--diff', diff,
      '--max-diff', '0.5',
    ], { encoding: 'utf8' });
    assert.equal(result.status, 1);
    assert.match(result.stdout, /"status":"failed"/);
    assert.equal(await stat(diff).then((value) => value.size > 0), true);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
