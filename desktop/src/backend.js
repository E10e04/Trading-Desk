'use strict';
// Lance et surveille le backend FastAPI. Pas de dépendance Electron : testable avec Node seul.
const { spawn } = require('node:child_process');
const net = require('node:net');
const crypto = require('node:crypto');
const http = require('node:http');
const path = require('node:path');
const fs = require('node:fs');

function freePort() {
  return new Promise((resolve, reject) => {
    const s = net.createServer();
    s.once('error', reject);
    s.listen(0, '127.0.0.1', () => { const { port } = s.address(); s.close(() => resolve(port)); });
  });
}

function health(port, timeoutMs = 1000) {
  return new Promise((resolve) => {
    const req = http.get({ host: '127.0.0.1', port, path: '/api/health', timeout: timeoutMs }, (res) => {
      let b = '';
      res.on('data', (c) => (b += c));
      res.on('end', () => { try { resolve(res.statusCode === 200 && JSON.parse(b).ok === true); } catch { resolve(false); } });
    });
    req.on('error', () => resolve(false));
    req.on('timeout', () => { req.destroy(); resolve(false); });
  });
}

/** Commande du backend : exécutable embarqué (application installée) ou Python du dépôt (développement). */
function backendCommand({ resourcesPath, repoRoot, platform = process.platform, env = process.env } = {}) {
  if (env.TRADING_BACKEND_CMD) return { cmd: env.TRADING_BACKEND_CMD, args: [], cwd: repoRoot };
  if (resourcesPath) {
    const exe = path.join(resourcesPath, 'backend', platform === 'win32' ? 'trading-backend.exe' : 'trading-backend');
    if (fs.existsSync(exe)) return { cmd: exe, args: [], cwd: path.dirname(exe), dist: path.join(resourcesPath, 'frontend') };
  }
  const py = platform === 'win32' ? path.join(repoRoot, 'backend', '.venv', 'Scripts', 'python.exe') : path.join(repoRoot, 'backend', '.venv', 'bin', 'python');
  return { cmd: fs.existsSync(py) ? py : 'python3', args: ['-m', 'app.main'], cwd: path.join(repoRoot, 'backend'), dist: path.join(repoRoot, 'frontend', 'dist') };
}

async function startBackend({ dataDir, resourcesPath, repoRoot, waitMs = 60000, env = process.env, platform } = {}) {
  const port = await freePort();
  const token = crypto.randomBytes(24).toString('hex');
  const c = backendCommand({ resourcesPath, repoRoot, platform, env });
  fs.mkdirSync(dataDir, { recursive: true });
  const child = spawn(c.cmd, c.args, {
    cwd: c.cwd,
    env: { ...env, BACKEND_HOST: '127.0.0.1', BACKEND_PORT: String(port), API_TOKEN: token, TRADING_DATA_DIR: dataDir, ...(c.dist ? { FRONTEND_DIST: c.dist } : {}) },
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  });
  let log = '';
  const keep = (d) => { log = (log + d).slice(-8000); };
  child.stdout.on('data', keep);
  child.stderr.on('data', keep);
  let exited = null;
  child.on('exit', (code) => { exited = code; });
  const t0 = Date.now();
  while (Date.now() - t0 < waitMs) {
    if (exited !== null) throw new Error(`Le backend s'est arrêté (code ${exited}).\n${log}`);
    if (await health(port)) return { port, token, url: `http://127.0.0.1:${port}`, child, log: () => log, stop: () => stopBackend(child) };
    await new Promise((r) => setTimeout(r, 300));
  }
  child.kill();
  throw new Error(`Le backend n'a pas répondu en ${waitMs / 1000} s.\n${log}`);
}

function stopBackend(child) {
  return new Promise((resolve) => {
    if (!child || child.exitCode !== null) return resolve();
    const t = setTimeout(() => child.kill('SIGKILL'), 5000);
    child.once('exit', () => { clearTimeout(t); resolve(); });
    child.kill();
  });
}

module.exports = { freePort, health, backendCommand, startBackend, stopBackend };
