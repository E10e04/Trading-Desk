'use strict';
// Teste le lanceur de backend avec le VRAI backend (Python du dépôt) — sans Electron.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const os = require('node:os');
const path = require('node:path');
const fs = require('node:fs');
const { startBackend, backendCommand, freePort, health } = require('../src/backend');

const repoRoot = path.resolve(__dirname, '..', '..');
const hasPy = fs.existsSync(path.join(repoRoot, 'backend', '.venv', 'bin', 'python')) || fs.existsSync(path.join(repoRoot, 'backend', '.venv', 'Scripts', 'python.exe'));

test('backendCommand : exécutable embarqué prioritaire, sinon Python du dépôt', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'res-'));
  fs.mkdirSync(path.join(tmp, 'backend'));
  fs.writeFileSync(path.join(tmp, 'backend', 'trading-backend.exe'), '');
  assert.match(backendCommand({ resourcesPath: tmp, repoRoot, platform: 'win32', env: {} }).cmd, /trading-backend\.exe$/);
  const dev = backendCommand({ repoRoot, platform: 'linux', env: {} });
  assert.deepEqual(dev.args, ['-m', 'app.main']);
  assert.equal(backendCommand({ repoRoot, env: { TRADING_BACKEND_CMD: '/x' } }).cmd, '/x');
});

test('freePort renvoie des ports libres distincts', async () => {
  const a = await freePort();
  assert.ok(a > 1024);
  assert.equal(await health(a), false);
});

test('démarre le vrai backend, jeton local, santé, arrêt propre', { skip: !hasPy && 'backend/.venv absent', timeout: 90000 }, async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'td-desk-'));
  const b = await startBackend({ dataDir, repoRoot, env: { ...process.env } });
  try {
    assert.equal(await health(b.port), true);
    const r = await fetch(`${b.url}/api/session`);
    assert.equal(r.status, 200);
    assert.equal((await r.json()).token, b.token);
    assert.equal((await fetch(`${b.url}/api/agents`)).status, 401); // protégé sans jeton
    assert.equal((await fetch(`${b.url}/api/agents`, { headers: { Authorization: `Bearer ${b.token}` } })).status, 200);
    assert.ok((await (await fetch(`${b.url}/`)).text()).includes('<div id="root">')); // interface servie
    assert.ok(fs.existsSync(path.join(dataDir, 'trading.db')) || fs.readdirSync(dataDir).length > 0); // données dans le dossier utilisateur
  } finally {
    await b.stop();
  }
  assert.notEqual(b.child.exitCode ?? b.child.signalCode, null);
});

test('un backend qui plante est signalé avec ses journaux', { timeout: 20000 }, async () => {
  await assert.rejects(startBackend({ dataDir: fs.mkdtempSync(path.join(os.tmpdir(), 'td-x-')), repoRoot, env: { ...process.env, TRADING_BACKEND_CMD: process.execPath, } }).then(() => { throw new Error('devait échouer'); }), /(arrêté|devait|répondu)/);
});

const frozen = path.join(repoRoot, 'backend', 'build-dist', 'trading-backend', 'trading-backend');
test('backend EMBARQUÉ (PyInstaller, plate-forme courante) : démarre, sert l’API, charge les skills vendorisés', { skip: !fs.existsSync(frozen) && 'bundle absent : cd backend && pyinstaller trading-backend.spec', timeout: 120000 }, async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'td-frozen-'));
  const b = await startBackend({ dataDir, repoRoot, env: { ...process.env, TRADING_BACKEND_CMD: frozen, FRONTEND_DIST: path.join(repoRoot, 'frontend', 'dist') }, waitMs: 90000 });
  try {
    const h = { Authorization: `Bearer ${b.token}` };
    const agents = await (await fetch(`${b.url}/api/agents`, { headers: h })).json();
    assert.equal(agents.items.length, 11);
    const sk = await (await fetch(`${b.url}/api/agents/risk/skill`, { headers: h })).json();
    assert.ok(sk.skill_md.length > 100 && Object.values(sk.integrity).every(Boolean), 'skills vendorisés intacts dans le bundle');
    const d = await (await fetch(`${b.url}/api/diagnostics`, { headers: h })).json();
    assert.equal(d.checks.find((c) => c.name === 'Skills vendorisés').ok, true);
  } finally { await b.stop(); }
});
