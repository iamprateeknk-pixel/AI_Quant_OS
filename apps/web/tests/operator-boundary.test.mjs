import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const source = readFileSync(new URL('../src/features/operator-session/boundary.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const boundary = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

test('mutations require the exact configured origin and host', () => {
  const request = new Request('http://127.0.0.1:3000/api/operator/session', { headers: { origin: 'http://127.0.0.1:3000', host: '127.0.0.1:3000' } });
  assert.doesNotThrow(() => boundary.requireOrigin(request, 'http://127.0.0.1:3000'));
  for (const origin of [null, 'https://evil.example', 'null', 'http://localhost:3000']) {
    const headers = new Headers({ host: '127.0.0.1:3000' });
    if (origin !== null) headers.set('origin', origin);
    assert.throws(() => boundary.requireOrigin(new Request(request.url, { headers }), 'http://127.0.0.1:3000'));
  }
  assert.throws(() => boundary.requireOrigin(new Request(request.url, { headers: { origin: 'http://127.0.0.1:3000', host: 'evil.example' } }), 'http://127.0.0.1:3000'));
});

test('credentials can only be forwarded to literal local API origins', () => {
  assert.equal(boundary.backendOrigin('http://127.0.0.1:8000'), 'http://127.0.0.1:8000');
  for (const url of ['http://localhost:8000', 'https://evil.example', 'http://127.0.0.1:8000/path', 'http://a:b@127.0.0.1:8000', 'http://127.0.0.1:8000?x=1', 'http://127.0.0.1:8000#x']) {
    assert.throws(() => boundary.backendOrigin(url));
  }
});

test('login accepts one bounded credential and never echoes invalid input', () => {
  assert.equal(boundary.loginCredential({ credential: 'synthetic-code' }), 'synthetic-code');
  for (const input of [{ credential: '' }, { credential: 'x'.repeat(513) }, { credential: 123 }, { credential: 'secret', extra: true }]) {
    assert.throws(() => boundary.loginCredential(input), (error) => !error.message.includes('secret'));
  }
});

test('stream body limits apply even when Content-Length is absent', async () => {
  assert.deepEqual(await boundary.readJson(new Request('http://local', { method: 'POST', body: '{"a":1}' })), { a: 1 });
  await assert.rejects(boundary.readJson(new Request('http://local', { method: 'POST', body: 'x'.repeat(4097) })));
  await assert.rejects(boundary.readJson(new Request('http://local', { method: 'POST', body: '{broken' })));
});

test('public session projection removes backend nonce and fingerprints', () => {
  const session = { mode: 'PAPER', session_id: '5df42392-b327-4b0a-bc98-6ea92c7c965c', created_at: '2026-10-08T12:00:00Z', expires_at: '2026-10-08T12:15:00Z', session_token: 'secret', token_sha256: 'private' };
  assert.deepEqual(boundary.publicSession(session), { mode: 'PAPER', session_id: session.session_id, created_at: session.created_at, expires_at: session.expires_at });
  assert.throws(() => boundary.publicSession({ ...session, mode: 'LIVE' }));
});

test('protected record reads can be bounded above the smaller credential body limit', async () => {
  const body = JSON.stringify({ records: 'x'.repeat(5000) });
  const request = () => new Request('http://local', { method: 'POST', body });
  await assert.rejects(boundary.readJson(request()));
  assert.equal((await boundary.readJson(request(), 8192)).records.length, 5000);
  await assert.rejects(boundary.readJson(request(), 4999));
  await assert.rejects(boundary.readJson(request(), 131073));
});
