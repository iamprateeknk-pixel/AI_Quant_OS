import { spawn } from 'node:child_process';
import { copyFile, mkdir } from 'node:fs/promises';
import net from 'node:net';
import path from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { chromium } from 'playwright';
import { compareVisualSuite } from '../tests/visual-suite.mjs';
import { createVisualFixtureServer, VISUAL_STATES } from './visual-fixtures.mjs';

const HOST = '127.0.0.1';
const VIEWPORT = { width: 1360, height: 900 };
const FIXED_TIME = new Date('2026-10-20T12:00:00.000Z');
const SESSION_TOKEN = 'synthetic-visual-session-00000000000000000000000000000000';
const outputDir = path.resolve('output/playwright/visual-captures');
const baselineDir = path.resolve('tests/fixtures/visual-baselines');
const diffDir = path.resolve('output/playwright/visual-diffs');

function parseArgs(args) {
  if (args.length === 0) return { updateBaselines: false };
  if (args.length === 1 && args[0] === '--update-baselines') {
    return { updateBaselines: true };
  }
  throw new Error('usage: npm run visual:regression [-- --update-baselines]');
}

async function freePort() {
  const server = net.createServer();
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, HOST, resolve);
  });
  const port = server.address().port;
  await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  return port;
}

async function waitForApp(child, origin) {
  const deadline = Date.now() + 45_000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error(`Next.js exited during startup with code ${child.exitCode}`);
    try {
      const response = await fetch(origin);
      if (response.status === 200) return;
    } catch {
      // The production server has not bound its loopback port yet.
    }
    await delay(250);
  }
  throw new Error('Next.js did not become ready within 45 seconds');
}

async function stopChild(child) {
  if (child.exitCode !== null) return;
  child.kill();
  await Promise.race([
    new Promise((resolve) => child.once('exit', resolve)),
    delay(5000),
  ]);
  if (child.exitCode === null && process.platform === 'win32') {
    const killer = spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore' });
    await new Promise((resolve) => killer.once('exit', resolve));
  }
}

function nextServer(fixtureOrigin, webOrigin, port) {
  const clock = path.resolve('scripts/visual-clock.cjs').replaceAll('\\', '/');
  const nodeOptions = [process.env.NODE_OPTIONS, `--require=${clock}`].filter(Boolean).join(' ');
  return spawn(process.execPath, [
    path.resolve('node_modules/next/dist/bin/next'),
    'start', '--hostname', HOST, '--port', String(port),
  ], {
    cwd: process.cwd(),
    env: {
      ...process.env,
      AI_QUANT_API_URL: fixtureOrigin,
      AI_QUANT_WEB_ORIGIN: webOrigin,
      NEXT_TELEMETRY_DISABLED: '1',
      NODE_OPTIONS: nodeOptions,
      TZ: 'UTC',
    },
    stdio: 'inherit',
  });
}

async function captureStates(page, origin, fixtures) {
  await mkdir(outputDir, { recursive: true });
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  for (const state of VISUAL_STATES) {
    fixtures.setState(state.name);
    const response = await page.goto(origin + state.path, { waitUntil: 'networkidle', timeout: 15_000 });
    if (response?.status() !== 200) {
      throw new Error(`${state.name} returned HTTP ${response?.status() ?? 'no response'}`);
    }
    if (state.path === '/') {
      await page.getByRole('heading', { name: 'Mission Control', exact: true }).waitFor();
      await page.getByText(state.safetyState, { exact: true }).first().waitFor();
    } else if (state.path === '/agents') {
      await page.getByRole('heading', { name: 'Registered agent versions' }).waitFor();
      await page.getByText(/The protected public agent registry refreshes every 15 seconds while the authenticated paper stream is current/).waitFor();
      try {
        await page.getByText(state.agentLifecycle, { exact: true }).waitFor();
      } catch (error) {
        console.error(`${state.name} rendered page:\n${(await page.locator('body').innerText()).slice(0, 4000)}`);
        throw error;
      }
    } else {
      await page.getByText('AGENT_FAILED', { exact: true }).waitFor();
    }
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.addStyleTag({ content: '*,*::before,*::after{animation-duration:0s!important;transition-duration:0s!important;caret-color:transparent!important}' });
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({
      path: path.join(outputDir, state.name),
      fullPage: true,
      animations: 'disabled',
    });
    if (pageErrors.length) throw new Error(`${state.name} browser errors: ${pageErrors.join('; ')}`);
  }
}

async function main() {
  const { updateBaselines } = parseArgs(process.argv.slice(2));
  if (updateBaselines && process.env.CI === 'true') {
    throw new Error('baseline updates are disabled in CI');
  }
  const fixtures = createVisualFixtureServer();
  let server;
  let browser;
  try {
    const fixtureOrigin = await fixtures.listen();
    const port = await freePort();
    const webOrigin = `http://${HOST}:${port}`;
    server = nextServer(fixtureOrigin, webOrigin, port);
    await waitForApp(server, webOrigin);

    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({
      viewport: VIEWPORT,
      deviceScaleFactor: 1,
      locale: 'en-GB',
      timezoneId: 'UTC',
    });
    await context.addCookies([{
      name: 'ai_quant_operator',
      value: SESSION_TOKEN,
      url: webOrigin,
      httpOnly: true,
      sameSite: 'Strict',
    }]);
    const page = await context.newPage();
    await page.addInitScript(() => {
      class VisualEventSource extends EventTarget {
        constructor(url) {
          super();
          this.url = url;
          this.readyState = 0;
          this.onerror = null;
          void fetch(url, { cache: 'no-store' }).then(async (response) => {
            if (!response.ok) throw new Error('Visual realtime fixture unavailable');
            const frame = await response.json();
            if (this.readyState === 2) return;
            this.readyState = 1;
            this.dispatchEvent(new MessageEvent('reset', {
              data: JSON.stringify(frame),
              lastEventId: `${frame.epoch}:${frame.sequence}`,
            }));
          }).catch(() => {
            if (this.readyState !== 2) this.onerror?.(new Event('error'));
          });
        }
        close() { this.readyState = 2; }
      }
      Object.defineProperty(window, 'EventSource', { configurable: true, value: VisualEventSource });
    });
    await page.clock.install({ time: FIXED_TIME });
    const fixtureClockStarted = Date.now();
    const fixtureNow = () => new Date(FIXED_TIME.getTime() + Date.now() - fixtureClockStarted - 50);
    await page.route('**/api/operator/session/authorization', (route) => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ authorized: true }),
    }));
    let realtimeSequence = 0;
    await page.route('**/api/realtime/events', (route) => {
      const frame = fixtures.realtimeFrame(++realtimeSequence, fixtureNow());
      return route.fulfill({
        status: 200,
        headers: { 'Cache-Control': 'no-store', 'Content-Type': 'application/json; charset=utf-8' },
        body: JSON.stringify(frame),
      });
    });
    await page.route('**/api/agents/observations', (route) => {
      return route.fulfill({
        status: 200,
        headers: { 'Cache-Control': 'no-store', 'Content-Type': 'application/json; charset=utf-8' },
        body: JSON.stringify(fixtures.agentObservation(fixtureNow())),
      });
    });
    await page.route('**/api/incidents/monitoring', (route) => route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'Synthetic visual fixture has no live stream.' }),
    }));
    await page.route('**/api/incidents/journal**', (route) => route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'Synthetic visual fixture has no journal history.' }),
    }));

    await captureStates(page, webOrigin, fixtures);
    await context.close();
    if (updateBaselines) {
      await mkdir(baselineDir, { recursive: true });
      for (const state of VISUAL_STATES) {
        await copyFile(path.join(outputDir, state.name), path.join(baselineDir, state.name));
      }
      console.log(`Updated ${VISUAL_STATES.length} visual baselines at ${baselineDir}`);
      return;
    }

    const result = await compareVisualSuite(baselineDir, outputDir, diffDir, {
      maxDiff: 0.002,
      pixelThreshold: 8,
    });
    console.log(JSON.stringify(result, null, 2));
    if (result.status !== 'passed') process.exitCode = 1;
  } finally {
    await browser?.close();
    await stopChild(server);
    await fixtures.close();
  }
}

main().catch((error) => {
  console.error(error.stack ?? error.message);
  process.exitCode = 1;
});
