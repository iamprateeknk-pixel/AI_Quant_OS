/** Synthetic local paper incidents only; no scan, recovery or capital actions. */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.AI_QUANT_PLAYWRIGHT_MODULE);
const browser = await chromium.launch({ headless: true, channel: 'msedge' });
const context = await browser.newContext({ viewport: { width: 1360, height: 900 } });
const page = await context.newPage();
const base = process.env.AI_QUANT_TEST_WEB_ORIGIN;
const errors = [];
page.on('pageerror', (error) => errors.push(error.message));
try {
  await page.goto(base + '/incidents');
  await page.getByText('Sign in to view protected health records.', { exact: true }).waitFor();
  assert.equal(await page.locator('tbody tr').count(), 0);
  await page.getByRole('link', { name: 'Sign in at Risk Center' }).click();
  await page.getByLabel('Operator access code', { exact: true }).fill('synthetic-health-browser-code-0094');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.getByText('Signed in until', { exact: false }).waitFor();
  const session = (await context.cookies()).find((cookie) => cookie.name === 'ai_quant_operator');
  assert(session?.httpOnly);
  await page.goto(base + '/incidents');
  if (process.env.AI_QUANT_TEST_HEALTH_DISABLED === '1') {
    await page.getByText('No health journal is connected.', { exact: false }).waitFor();
    assert.equal(await page.getByRole('heading', { name: 'Verified incident timeline' }).count(), 0);
    assert.equal(await page.getByText('Recorded open incidents', { exact: true }).locator('..').getByText('Unavailable', { exact: true }).count(), 1);
    console.log(JSON.stringify({ status: 'passed', disabled_no_fabricated_health: true }));
  } else {
    await page.getByRole('heading', { name: 'Verified incident timeline' }).waitFor();
    assert.equal(await page.locator('tbody tr').count(), 25);
    assert.equal(await page.getByText('Recorded incidents and quarantine restrictions were restored.', { exact: false }).count(), 1);
    assert.equal(await page.getByText('A02@0.2.0', { exact: true }).count(), 2);
    const detail = page.locator('.health-record summary').first();
    await detail.focus(); await page.keyboard.press('Enter');
    assert.equal(await page.getByText('Incident ID', { exact: true }).first().isVisible(), true);
    assert(!(await page.content()).includes('synthetic-health-evidence-not-for-ui'));
    assert(!(await page.content()).includes(session.value));
    await page.screenshot({ path: process.env.AI_QUANT_TEST_SCREENSHOT, fullPage: false });
    await page.getByRole('link', { name: 'Older incidents', exact: true }).click();
    await page.getByText('Showing 26–27 of 27', { exact: true }).waitFor();
    assert.equal(await page.locator('tbody tr').count(), 2);
    await page.getByRole('link', { name: 'Previous incidents', exact: true }).click();
    await page.getByText('Showing 1–25 of 27', { exact: true }).waitFor();
    await page.goto(base + '/incidents?offset=25&head=' + '0'.repeat(64));
    await page.getByText('The incident journal changed.', { exact: false }).waitFor();
    assert.equal(await page.locator('tbody tr').count(), 0);
    await page.getByRole('link', { name: 'Reload first page', exact: true }).click();
    await page.getByText('Showing 1–25 of 27', { exact: true }).waitFor();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.reload();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.locator('.health-record summary').first().click();
    assert.equal(await page.locator('.health-record summary').first().evaluate((node) => node.getBoundingClientRect().height >= 44), true);
    await page.screenshot({ path: process.env.AI_QUANT_TEST_SCREENSHOT.replace('desktop', 'mobile'), fullPage: false });
    console.log(JSON.stringify({ status: 'passed', protected_records: 27, snapshot_pages: true, changed_head_withholds_rows: true, keyboard_details: true, mobile_width: 390, raw_evidence_and_nonce_absent: true }));
  }
  await page.goto(base + '/risk');
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await page.getByRole('button', { name: 'Sign in', exact: true }).waitFor();
  await page.goto(base + '/incidents');
  await page.getByText('Sign in to view protected health records.', { exact: true }).waitFor();
  assert.equal(await page.locator('tbody tr').count(), 0);
  assert.deepEqual(errors, []);
} catch (error) {
  console.error(JSON.stringify({ failed_url: page.url(), visible_state: (await page.locator('.page-content').innerText()).slice(0, 2000) }));
  throw error;
} finally { await context.close(); await browser.close(); }
