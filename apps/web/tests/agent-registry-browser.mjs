/** Read-only verification against the real local paper registry. */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.AI_QUANT_PLAYWRIGHT_MODULE);
const browser = await chromium.launch({ headless: true, channel: 'msedge' });
const context = await browser.newContext({ viewport: { width: 1360, height: 900 } });
const page = await context.newPage();
const failures = [];
page.on('pageerror', (error) => failures.push(error.message));
try {
  if (process.env.AI_QUANT_TEST_REGISTRY_UNAVAILABLE === '1') {
    await page.goto(process.env.AI_QUANT_TEST_WEB_ORIGIN + '/agents');
    await page.getByRole('status').filter({ hasText: 'Agent registry unavailable' }).waitFor();
    assert.equal(await page.locator('tbody tr').count(), 0);
    assert.deepEqual(failures, []);
    console.log(JSON.stringify({ status: 'passed', unavailable_withholds_registry: true, page_errors: failures }));
  } else {
  const response = await context.request.get(process.env.AI_QUANT_TEST_API_ORIGIN + '/api/v1/agents/registry');
  assert.equal(response.status(), 200);
  const registry = await response.json();
  assert.equal(registry.mode, 'PAPER');
  assert.equal(registry.agents.length, 29);
  assert.equal(new Set(registry.agents.map((agent) => agent.agent_id)).size, 18);
  await page.goto(process.env.AI_QUANT_TEST_WEB_ORIGIN + '/agents');
  await page.getByRole('heading', { name: 'Registered agent versions' }).waitFor();
  assert.equal(await page.locator('tbody tr').count(), registry.agents.length);
  assert.equal(await page.getByText('No authoritative data source connected').count(), 0);
  for (const agent of registry.agents) {
    const row = page.locator('tbody tr').filter({ has: page.getByText(`View contract for ${agent.agent_id} v${agent.agent_version}`, { exact: true }) });
    assert.equal(await row.getByText(agent.implementation_status, { exact: true }).count(), 1);
    assert.equal(await row.getByText(agent.lifecycle, { exact: true }).count(), 1);
  }
  const contract = page.getByText('View contract for A02 v0.2.0', { exact: true });
  await contract.focus();
  await page.keyboard.press('Enter');
  const reference = registry.agents.find((agent) => agent.agent_id === 'A02' && agent.agent_version === '0.2.0').manifest_ref;
  assert.equal(await page.getByText(reference, { exact: true }).isVisible(), true);
  await page.screenshot({ path: process.env.AI_QUANT_TEST_SCREENSHOT, fullPage: false });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await page.getByRole('heading', { name: 'Registered agent versions' }).waitFor();
  await page.getByText('View contract for A02 v0.2.0', { exact: true }).click();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  assert.equal(await page.locator('.agent-contract summary').first().evaluate((node) => node.getBoundingClientRect().height >= 44), true);
  assert.deepEqual(failures, []);
  console.log(JSON.stringify({ status: 'passed', real_backend_versions: registry.agents.length, roles: 18, keyboard_contract: true, mobile_width: 390, page_errors: failures }));
  }
} finally {
  await context.close();
  await browser.close();
}
