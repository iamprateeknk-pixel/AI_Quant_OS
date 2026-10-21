import { mkdir, stat } from 'node:fs/promises';
import path from 'node:path';
import { compareVisualImages } from './visual-diff.mjs';

export const MISSION_CONTROL_VISUAL_STATES = Object.freeze([
  'normal-paper-dashboard.png',
  'paper-mode-no-new-risk-dashboard.png',
  'broker-disconnected.png',
  'agent-quarantined.png',
  'incident-active.png',
  'agent-shadow-registry.png',
]);

async function imageExists(file) {
  try {
    return (await stat(file)).isFile();
  } catch (error) {
    if (error.code === 'ENOENT' || error.code === 'ENOTDIR') return false;
    throw error;
  }
}

export async function compareVisualSuite(referenceDir, currentDir, diffDir, options = {}) {
  const { maxDiff = 0, pixelThreshold = 0, maskRectsByState = {} } = options;
  if (typeof maxDiff !== 'number' || !Number.isFinite(maxDiff) || maxDiff < 0 || maxDiff > 1) {
    throw new TypeError('maxDiff must be a number between 0 and 1');
  }
  await mkdir(diffDir, { recursive: true });
  const states = [];
  for (const name of MISSION_CONTROL_VISUAL_STATES) {
    const reference = path.join(referenceDir, name);
    const current = path.join(currentDir, name);
    const [hasReference, hasCurrent] = await Promise.all([imageExists(reference), imageExists(current)]);
    if (!hasReference || !hasCurrent) {
      states.push({
        name,
        status: !hasReference && !hasCurrent
          ? 'missing_reference_and_capture'
          : !hasReference ? 'missing_reference' : 'missing_capture',
      });
      continue;
    }
    const diffPath = path.join(diffDir, name);
    try {
      const result = await compareVisualImages(reference, current, {
        diffPath,
        pixelThreshold,
        maskRects: maskRectsByState[name] ?? [],
      });
      states.push({ name, status: result.changedRatio <= maxDiff ? 'passed' : 'failed', ...result, diff: diffPath });
    } catch (error) {
      states.push({ name, status: 'invalid', error: error.message.slice(0, 240) });
    }
  }
  return {
    status: states.every((state) => state.status === 'passed') ? 'passed' : 'failed',
    maxDiff,
    pixelThreshold,
    states,
  };
}
