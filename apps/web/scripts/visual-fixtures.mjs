import { createServer } from 'node:http';

const FIXED_TIME = '2026-10-20T12:00:00Z';
const WORLD_VERSION = `world:${'a'.repeat(64)}`;
const MANIFEST = `agent-manifest:${'b'.repeat(64)}`;
const INCIDENT_ID = 'c'.repeat(64);

export const VISUAL_STATES = Object.freeze([
  Object.freeze({ name: 'normal-paper-dashboard.png', path: '/', safetyState: 'NORMAL', brokerState: 'UNKNOWN', agentLifecycle: 'SHADOW', incidentActive: false }),
  Object.freeze({ name: 'paper-mode-no-new-risk-dashboard.png', path: '/', safetyState: 'NO_NEW_RISK', brokerState: 'UNKNOWN', agentLifecycle: 'SHADOW', incidentActive: false }),
  Object.freeze({ name: 'broker-disconnected.png', path: '/', safetyState: 'NORMAL', brokerState: 'DISCONNECTED', agentLifecycle: 'SHADOW', incidentActive: false }),
  Object.freeze({ name: 'agent-quarantined.png', path: '/agents', safetyState: 'NORMAL', brokerState: 'UNKNOWN', agentLifecycle: 'QUARANTINED', incidentActive: false }),
  Object.freeze({ name: 'incident-active.png', path: '/incidents', safetyState: 'NO_NEW_RISK', brokerState: 'UNKNOWN', agentLifecycle: 'QUARANTINED', incidentActive: true }),
  Object.freeze({ name: 'agent-shadow-registry.png', path: '/agents', safetyState: 'NORMAL', brokerState: 'UNKNOWN', agentLifecycle: 'SHADOW', incidentActive: false }),
]);

const brokerIds = ['PAPER', 'EXNESS_MT5_DEMO', 'EXNESS', 'IBKR', 'ZERODHA'];

function activeState(stateName) {
  const state = VISUAL_STATES.find((candidate) => candidate.name === stateName);
  if (!state) throw new Error(`unknown visual state: ${stateName}`);
  return state;
}

function brokerHealth(state) {
  return {
    schema_version: '1.0',
    mode: 'PAPER',
    live_execution_released: false,
    brokers: brokerIds.map((broker_id) => ({
      schema_version: '1.0',
      mode: 'PAPER',
      broker_id,
      connection_state: broker_id === 'PAPER' ? state.brokerState : 'UNKNOWN',
      quote_age_ms: null,
      order_latency_ms: null,
      reject_rate: null,
      reconciliation_state: 'UNASSESSED',
      spread_state: 'UNASSESSED',
      account_state: 'UNASSESSED',
      health_score: null,
      updated_at: FIXED_TIME,
      live_execution_released: false,
    })),
  };
}

function registry(state) {
  return {
    schema_version: '1.0',
    mode: 'PAPER',
    agents: [{
      schema_version: '1.0',
      agent_id: 'A02',
      agent_version: '0.1.0',
      name: 'Regime analyst',
      squad: 'Market',
      mission: 'Classify deterministic PAPER market regimes.',
      output_type: 'RegimeEvidence',
      required_inputs: ['world_state'],
      allowed_tools: ['read_world_state'],
      implementation_status: 'PARTIAL',
      timeout_seconds: 2,
      manifest_ref: MANIFEST,
      lifecycle: state.agentLifecycle,
    }],
  };
}

function healthStatus(state) {
  return {
    schema_version: '1.0',
    mode: 'PAPER',
    enabled: state.incidentActive,
    state: state.incidentActive ? 'MONITORING' : 'DISABLED',
    ...(state.incidentActive ? {
      open_incidents: 1,
      quarantined_versions: ['A02@0.1.0'],
      last_tick: FIXED_TIME,
      last_error: null,
      promotion_allowed: false,
    } : {}),
  };
}

function healthTimeline(state) {
  const incidents = state.incidentActive ? [{
    schema_version: '1.0',
    incident_id: INCIDENT_ID,
    code: 'AGENT_FAILED',
    subject: 'visual-fixture-agent-run',
    detected_at: FIXED_TIME,
    agent_id: 'A02',
    agent_version: '0.1.0',
    manifest_ref: MANIFEST,
    safety_state: 'NO_NEW_RISK',
    quarantine: true,
    status: 'OPEN',
    transitions: [],
    promotion_allowed: false,
  }] : [];
  return {
    schema_version: '1.0',
    mode: 'PAPER',
    head_hash: INCIDENT_ID,
    total: incidents.length,
    offset: 0,
    limit: 25,
    next_offset: null,
    incidents,
  };
}

function payloadFor(pathname, state) {
  if (pathname === '/api/v1/system/status') {
    return { mode: 'PAPER', safety_state: state.safetyState, order_count: 0 };
  }
  if (pathname === '/api/v1/orders') return { orders: [] };
  if (pathname === '/api/v1/portfolio/currencies') {
    return {
      mode: 'PAPER', schema_version: '1.0', reporting_currency: 'USD',
      valuation_available: false, marked_pnl_reporting: null,
      gross_worst_case_exposure_reporting: null, currencies: [],
    };
  }
  if (pathname === '/api/v1/portfolio/positions') {
    return { mode: 'PAPER', schema_version: '1.0', positions: [] };
  }
  if (pathname === '/api/v1/fills') return { fills: [] };
  if (pathname === '/api/v1/markets/EURUSD') {
    return {
      instrument_id: 'EURUSD', bid: '1.08001', ask: '1.08003',
      observed_at: FIXED_TIME, world_state_version: WORLD_VERSION,
      freshness: 'FRESH', source: 'PAPER_SIMULATED',
    };
  }
  if (pathname === '/api/v1/brokers/health') return brokerHealth(state);
  if (pathname === '/api/v1/agents/registry') return registry(state);
  if (pathname === '/api/v1/paper/health') return healthStatus(state);
  if (pathname === '/api/v1/paper/health/incidents/page') return healthTimeline(state);
  if (pathname === '/api/v1/observability/alerts') {
    return {
      schema_version: '1.0', mode: 'PAPER',
      source_kind: 'LOCAL_PROMETHEUS_RULE_EVALUATION', durable: false,
      notification_verified: false, independently_attested: false,
      observed_at: FIXED_TIME, state: 'DISABLED', rules: [],
    };
  }
  return null;
}

function respond(response, status, body) {
  response.writeHead(status, {
    'Cache-Control': 'no-store',
    'Content-Type': 'application/json; charset=utf-8',
  });
  response.end(JSON.stringify(body));
}

export function createVisualFixtureServer() {
  let stateName = VISUAL_STATES[0].name;
  const server = createServer((request, response) => {
    const url = new URL(request.url, 'http://127.0.0.1');
    const body = payloadFor(url.pathname, activeState(stateName));
    if (body === null || request.method !== 'GET') {
      respond(response, 404, { detail: 'Visual fixture route not found.' });
      return;
    }
    respond(response, 200, body);
  });

  let origin;
  return {
    get origin() {
      if (!origin) throw new Error('visual fixture server is not listening');
      return origin;
    },
    async listen() {
      if (origin) throw new Error('visual fixture server is already listening');
      await new Promise((resolve, reject) => {
        server.once('error', reject);
        server.listen(0, '127.0.0.1', resolve);
      });
      origin = `http://127.0.0.1:${server.address().port}`;
      return origin;
    },
    setState(name) {
      activeState(name);
      stateName = name;
    },
    agentObservation(observedAt = new Date(FIXED_TIME)) {
      const state = activeState(stateName);
      return {
        observed_at: new Date(observedAt).toISOString(),
        registry: { ok: true, value: registry(state).agents },
      };
    },
    realtimeFrame(sequence, capturedAt = new Date(FIXED_TIME)) {
      const state = activeState(stateName);
      const captured = new Date(capturedAt);
      const validUntil = new Date(captured.getTime() + 3000).toISOString();
      return {
        schema_version: '1.0',
        mode: 'PAPER',
        epoch: '12345678-1234-4123-8123-123456789abc',
        sequence,
        captured_at: captured.toISOString(),
        valid_until: validUntil,
        safety_state: state.safetyState,
        order_count: 0,
        world: {
          schema_version: '1.0',
          source: 'PAPER_SIMULATED',
          world_state_version: WORLD_VERSION,
          as_of: captured.toISOString(),
          valid_until: new Date(captured.getTime() + 5000).toISOString(),
          status: 'READY',
          instruments: [{
            instrument_id: 'EURUSD', quote_status: 'FRESH', history_status: 'FRESH',
            bar_count: 1, regime: 'TRENDING', mid_price: '1.08002', spread_bps: '0.185',
            return_bps: '0.1', realized_volatility_bps: '0.2',
          }],
          currency_twins: [
            { currency: 'EUR', instrument_ids: ['EURUSD'], fresh_quote_count: 1, macro_available: false },
            { currency: 'USD', instrument_ids: ['EURUSD'], fresh_quote_count: 1, macro_available: false },
          ],
        },
      };
    },
    async close() {
      if (!server.listening) return;
      await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
      origin = undefined;
    },
  };
}
