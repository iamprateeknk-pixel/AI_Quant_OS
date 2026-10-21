#!/usr/bin/env node
import path from 'node:path';
import { compareVisualImages } from '../tests/visual-diff.mjs';

function parseArgs(args) {
  const options = { masks: [] };
  for (let index = 0; index < args.length; index += 1) {
    const flag = args[index];
    const value = args[index + 1];
    if (!value || value.startsWith('--')) throw new Error(`missing value for ${flag}`);
    if (flag === '--reference' || flag === '--current' || flag === '--diff') options[flag.slice(2)] = value;
    else if (flag === '--max-diff' || flag === '--pixel-threshold') {
      const parsed = Number(value);
      if (!Number.isFinite(parsed)) throw new Error(`${flag} must be numeric`);
      options[flag === '--max-diff' ? 'maxDiff' : 'pixelThreshold'] = parsed;
    } else if (flag === '--mask') {
      const parts = value.split(',').map(Number);
      if (parts.length !== 4 || !parts.every(Number.isInteger)) throw new Error('--mask uses x,y,width,height integer coordinates');
      options.masks.push({ x: parts[0], y: parts[1], width: parts[2], height: parts[3] });
    } else throw new Error(`unknown option: ${flag}`);
    index += 1;
  }
  if (!options.reference || !options.current) throw new Error('usage: --reference <baseline.png> --current <capture.png> [--diff <diff.png>] [--max-diff 0.01] [--pixel-threshold 8] [--mask x,y,width,height]');
  options.maxDiff ??= 0;
  options.pixelThreshold ??= 0;
  if (options.maxDiff < 0 || options.maxDiff > 1) throw new Error('--max-diff must be between 0 and 1');
  if (!options.diff) options.diff = path.join(path.dirname(options.current), `${path.basename(options.current, path.extname(options.current))}.diff.png`);
  return options;
}

try {
  const options = parseArgs(process.argv.slice(2));
  const result = await compareVisualImages(options.reference, options.current, {
    diffPath: options.diff,
    pixelThreshold: options.pixelThreshold,
    maskRects: options.masks,
  });
  const passed = result.changedRatio <= options.maxDiff;
  console.log(JSON.stringify({ status: passed ? 'passed' : 'failed', ...result, maxDiff: options.maxDiff, diff: options.diff }));
  if (!passed) process.exitCode = 1;
} catch (error) {
  console.error(error.message);
  process.exitCode = 2;
}
