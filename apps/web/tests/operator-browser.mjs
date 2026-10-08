/** Real local paper services only; supplied credentials must be synthetic. */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.AI_QUANT_PLAYWRIGHT_MODULE);
const base = process.env.AI_QUANT_TEST_WEB_ORIGIN;
const code = 'synthetic-operator-browser-code-0090';
const browser = await chromium.launch({ headless: true, channel: 'msedge' });
const context = await browser.newContext({ viewport: { width: 1360, height: 900 } });
const page = await context.newPage();
try {
  await page.goto(base + '/risk');
  await page.getByLabel('Operator access code', { exact: true }).waitFor({ timeout: 5000 });
  assert.equal(await page.getByRole('button', { name: 'Apply paper safety action' }).isEnabled(), false);
  await page.getByLabel('Operator access code', { exact: true }).fill(code);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.getByText('Signed in until', { exact: false }).waitFor();
  const cookie = (await context.cookies()).find((item) => item.name === 'ai_quant_operator');
  assert(cookie?.httpOnly && cookie.sameSite === 'Strict');
  assert(!await page.evaluate(() => document.cookie.includes('ai_quant_operator')));
  const session = await context.request.get(base + '/api/operator/session');
  assert.equal(session.status(), 200);
  assert(!(await session.text()).includes(cookie.value));
  const crossOrigin = await context.request.post(base + '/api/operator/controls', { headers: { Origin: 'https://evil.example' }, data: {} });
  assert.equal(crossOrigin.status(), 403);
  await page.getByLabel('Reason for action').fill('Synthetic paper browser verification');
  await page.getByLabel('Re-enter access code').fill('wrong-synthetic-code');
  await page.getByRole('button', { name: 'Apply paper safety action' }).click();
  await page.getByRole('button', { name: 'Retry same action' }).waitFor();
  await page.getByLabel('Re-enter access code').fill(code);
  await page.route('**/api/operator/controls', async (route) => {
    await route.fetch();
    await route.abort('failed');
  }, { times: 1 });
  await page.getByRole('button', { name: 'Retry same action' }).click();
  const retained = await page.evaluate(() => sessionStorage.getItem('ai_quant_pending_control_v1'));
  assert(retained && !retained.includes(code) && !retained.includes('credential'));
  await page.reload();
  assert.equal(await page.evaluate(() => sessionStorage.getItem('ai_quant_pending_control_v1')), retained);
  await page.getByRole('button', { name: 'Check receipt' }).waitFor();
  await page.route('**/api/operator/controls/*', async (route) => {
    const response = await route.fetch();
    const terminal = await response.json();
    await route.fulfill({ response, json: { ...terminal, decision: 'PENDING',
      authoritative_state: terminal.previous_state,
      policy_checks: ['PAPER_ONLY','VALID_SESSION','FRESH_REAUTH','RESERVED_NOT_APPLIED'] } });
  }, { times: 1 });
  await page.getByRole('button', { name: 'Check receipt' }).click();
  await page.getByText('Backend pending: original command reserved.', { exact: false }).waitFor();
  assert.equal(await page.evaluate(() => sessionStorage.getItem('ai_quant_pending_control_v1')), retained);
  await page.getByRole('button', { name: 'Check receipt' }).click();
  await page.getByText('Backend accepted: NO_NEW_RISK', { exact: false }).waitFor();
  await page.getByText('Expected backend state: NO_NEW_RISK', { exact: false }).waitFor();
  await page.getByLabel('Paper safety action').selectOption('FLATTEN_ONLY');
  await page.getByLabel('Reason for action').fill('Synthetic paper exposure reduction verification');
  await page.getByLabel('Re-enter access code').fill(code);
  await page.getByLabel('Type FLATTEN_ONLY to confirm').fill('FLATTEN_ONLY');
  await page.getByRole('button', { name: 'Apply paper safety action' }).click();
  await page.getByText('Backend accepted: FLATTEN_ONLY', { exact: false }).waitFor();
  await page.getByText('Expected backend state: FLATTEN_ONLY', { exact: false }).waitFor();
  await page.getByLabel('Paper safety action').selectOption('HALT');
  await page.getByLabel('Reason for action').fill('Synthetic paper halt verification');
  await page.getByLabel('Re-enter access code').fill(code);
  assert.equal(await page.getByRole('button', { name: 'Apply paper safety action' }).isEnabled(), false);
  await page.getByLabel('Type HALT to confirm').fill('HALT');
  await page.getByRole('button', { name: 'Apply paper safety action' }).click();
  await page.getByText('Backend accepted: HALT', { exact: false }).waitFor();
  await page.getByText('Expected backend state: HALT', { exact: false }).waitFor();
  await page.getByLabel('Paper safety action').selectOption('NO_NEW_RISK');
  await page.getByLabel('Reason for action').fill('Synthetic forbidden safety recovery');
  await page.getByLabel('Re-enter access code').fill(code);
  await page.getByRole('button', { name: 'Apply paper safety action' }).click();
  await page.getByText('Backend rejected: SAFETY_RECOVERY_FORBIDDEN', { exact: false }).waitFor();
  assert.equal(await page.evaluate(() => sessionStorage.getItem('ai_quant_pending_control_v1')), null);
  await page.setViewportSize({ width: 390, height: 844 });
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
  await page.screenshot({ path: process.env.AI_QUANT_TEST_SCREENSHOT, fullPage: true });
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await page.getByLabel('Operator access code', { exact: true }).waitFor();
  assert.equal((await context.request.get(base + '/api/operator/session')).status(), 401);
  console.log('PASS: login, private cookie, origin rejection, failed reauth, same-action retry, dropped acknowledgement and reload recovery, NO_NEW_RISK, typed FLATTEN_ONLY/HALT, durable recovery rejection, mobile overflow and logout');
} catch (error) {
  console.log(await page.locator('.operator-console').innerText().catch(() => 'Operator panel unavailable'));
  await page.screenshot({ path: process.env.AI_QUANT_TEST_SCREENSHOT, fullPage: true });
  throw error;
} finally { await browser.close(); }
