import assert from 'node:assert/strict';
import test from 'node:test';
import { createVisualFixtureServer, VISUAL_STATES } from '../scripts/visual-fixtures.mjs';

test('visual fixtures expose the six deterministic PAPER states', async () => {
  const fixtures = createVisualFixtureServer();
  await fixtures.listen();
  try {
    assert.deepEqual(VISUAL_STATES.map((state) => state.name), [
      'normal-paper-dashboard.png',
      'paper-mode-no-new-risk-dashboard.png',
      'broker-disconnected.png',
      'agent-quarantined.png',
      'incident-active.png',
      'agent-shadow-registry.png',
    ]);

    for (const state of VISUAL_STATES) {
      fixtures.setState(state.name);
      const response = await fetch(`${fixtures.origin}/api/v1/system/status`);
      assert.equal(response.status, 200);
      const snapshot = await response.json();
      assert.equal(snapshot.mode, 'PAPER');
      assert.equal(snapshot.safety_state, state.safetyState);
    }

    fixtures.setState('broker-disconnected.png');
    const healthResponse = await fetch(`${fixtures.origin}/api/v1/brokers/health`);
    const health = await healthResponse.json();
    assert.equal(health.brokers.find((row) => row.broker_id === 'PAPER').connection_state, 'DISCONNECTED');

    fixtures.setState('agent-quarantined.png');
    const quarantineResponse = await fetch(`${fixtures.origin}/api/v1/agents/registry`);
    const quarantineRegistry = await quarantineResponse.json();
    assert(quarantineRegistry.agents.some((agent) => agent.lifecycle === 'QUARANTINED'));

    fixtures.setState('agent-shadow-registry.png');
    const shadowResponse = await fetch(`${fixtures.origin}/api/v1/agents/registry`);
    const shadowRegistry = await shadowResponse.json();
    assert(shadowRegistry.agents.some((agent) => agent.lifecycle === 'SHADOW'));

    fixtures.setState('incident-active.png');
    const incidentResponse = await fetch(`${fixtures.origin}/api/v1/paper/health/incidents/page?limit=25&offset=0`);
    const incidentPage = await incidentResponse.json();
    assert.equal(incidentPage.incidents[0].code, 'AGENT_FAILED');
  } finally {
    await fixtures.close();
  }
});

test('visual fixture server rejects unknown states and non-fixture routes', async () => {
  const fixtures = createVisualFixtureServer();
  await fixtures.listen();
  try {
    assert.throws(() => fixtures.setState('unknown.png'), /unknown visual state/);
    const response = await fetch(`${fixtures.origin}/api/v1/live/orders`);
    assert.equal(response.status, 404);
  } finally {
    await fixtures.close();
  }
});
