#!/usr/bin/env node
import path from 'node:path';
import { readFile } from 'node:fs/promises';
import { compareVisualSuite } from '../tests/visual-suite.mjs';

function parseArgs(args) {
  const options = {};
  for (let index = 0; index < args.length; index += 1) {
    const flag = args[index];
    const value = args[index + 1];
    if (!value || value.startsWith('--')) throw new Error(`missing value for ${flag}`);
    if (flag === '--reference-dir' || flag === '--current-dir' || flag === '--diff-dir' || flag === '--masks-json') options[flag.slice(2).replaceAll('-', '')] = value;
    else if (flag === '--max-diff' || flag === '--pixel-threshold') {
      const parsed = Number(value);
      if (!Number.isFinite(parsed)) throw new Error(`${flag} must be numeric`);
      options[flag === '--max-diff' ? 'maxDiff' : 'pixelThreshold'] = parsed;
    } else throw new Error(`unknown option: ${flag}`);
    index += 1;
  }
  if (!options.referencedir || !options.currentdir) throw new Error('usage: --reference-dir <dir> --current-dir <dir> [--diff-dir <dir>] [--max-diff 0.01] [--pixel-threshold 8] [--masks-json <file>]');
  options.diffdir ??= path.resolve('output/playwright/visual-diffs');
  options.maxDiff ??= 0;
  options.pixelThreshold ??= 0;
  return options;
}

try {
  const options = parseArgs(process.argv.slice(2));
  const maskRectsByState = options.masksjson
    ? JSON.parse(await readFile(options.masksjson, 'utf8'))
    : {};
  const result = await compareVisualSuite(options.referencedir, options.currentdir, options.diffdir, {
    maxDiff: options.maxDiff,
    pixelThreshold: options.pixelThreshold,
    maskRectsByState,
  });
  console.log(JSON.stringify(result));
  if (result.status !== 'passed') process.exitCode = 1;
} catch (error) {
  console.error(error.message);
  process.exitCode = 2;
}
