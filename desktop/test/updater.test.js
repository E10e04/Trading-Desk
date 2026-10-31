'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const crypto = require('node:crypto');
const os = require('node:os');
const fs = require('node:fs');
const path = require('node:path');
const { isAllowedAsset, installUpdate } = require('../src/updater');

const BODY = Buffer.from('MZ-faux-installateur'.repeat(500));
const SHA = crypto.createHash('sha256').update(BODY).digest('hex');
function server() {
  return new Promise((resolve) => {
    const s = http.createServer((req, res) => {
      if (req.url === '/redir') { res.writeHead(302, { Location: '/file.exe' }); return res.end(); }
      if (req.url === '/404') { res.writeHead(404); return res.end(); }
      res.writeHead(200, { 'Content-Length': BODY.length }); res.end(BODY);
    }).listen(0, '127.0.0.1', () => resolve({ s, base: `http://127.0.0.1:${s.address().port}` }));
  });
}
const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), 'upd-'));

test('seules les URL de release du dépôt configuré sont autorisées', () => {
  assert.equal(isAllowedAsset('https://github.com/E10e04/Trading-Desk/releases/download/v1.0.0/a.exe', 'E10e04/Trading-Desk'), true);
  for (const bad of ['http://github.com/E10e04/Trading-Desk/releases/download/v1/a.exe', 'https://evil.com/E10e04/Trading-Desk/releases/download/v1/a.exe',
    'https://github.com/autre/repo/releases/download/v1/a.exe', 'https://github.com/E10e04/Trading-Desk/archive/main.zip', 'pas une url']) assert.equal(isAllowedAsset(bad, 'E10e04/Trading-Desk'), false, bad);
});

test('Windows : télécharge (avec redirection), vérifie le SHA-256, lance l’installateur détaché puis quitte', async () => {
  const { s, base } = await server();
  let spawned = null; let quit = false; const progress = [];
  try {
    const r = await installUpdate({ name: 'Setup.exe', url: `${base}/redir`, sha256: SHA }, { platform: 'win32', tmpDir: tmp(), insecureForTests: true, onProgress: (p) => progress.push(p),
      spawnImpl: (cmd, args, opts) => { spawned = { cmd, opts }; return { unref() {} }; }, quit: () => { quit = true; } });
    assert.equal(fs.readFileSync(r.file).equals(BODY), true);
    assert.equal(spawned.cmd, r.file); assert.equal(spawned.opts.detached, true);
    assert.equal(quit, true); assert.ok(progress.at(-1) === 1);
  } finally { s.close(); }
});

test('macOS : ouvre le DMG téléchargé', async () => {
  const { s, base } = await server();
  let opened = null;
  try {
    await installUpdate({ name: 'App.dmg', url: `${base}/file`, sha256: SHA }, { platform: 'darwin', tmpDir: tmp(), insecureForTests: true, openPath: async (f) => { opened = f; return ''; }, quit: () => {} });
    assert.ok(opened.endsWith('App.dmg'));
  } finally { s.close(); }
});

test('empreinte incorrecte : fichier supprimé, rien n’est lancé', async () => {
  const { s, base } = await server();
  const dir = tmp(); let spawned = false;
  try {
    await assert.rejects(installUpdate({ name: 'Setup.exe', url: `${base}/file`, sha256: 'a'.repeat(64) }, { platform: 'win32', tmpDir: dir, insecureForTests: true, spawnImpl: () => { spawned = true; return {}; }, quit: () => {} }), /SHA-256/);
    assert.equal(spawned, false); assert.equal(fs.existsSync(path.join(dir, 'Setup.exe')), false);
  } finally { s.close(); }
});

test('refus : URL hors dépôt, mauvaise extension, nom avec chemin, HTTP 404, Linux', async () => {
  const { s, base } = await server();
  const o = { platform: 'win32', tmpDir: tmp(), quit() {}, spawnImpl: () => ({}) };
  try {
    await assert.rejects(installUpdate({ name: 'a.exe', url: 'https://evil.com/a.exe' }, o), /non autorisée/);
    await assert.rejects(installUpdate({ name: 'a.dmg', url: `${base}/file` }, { ...o, insecureForTests: true }), /\.exe attendu/);
    await assert.rejects(installUpdate({ name: '../a.exe', url: `${base}/file` }, { ...o, insecureForTests: true }), /inattendu/);
    await assert.rejects(installUpdate({ name: 'a.exe', url: `${base}/404` }, { ...o, insecureForTests: true }), /HTTP 404/);
    await assert.rejects(installUpdate({ name: 'a.exe', url: `${base}/file` }, { ...o, platform: 'linux', insecureForTests: true }), /non prise en charge/);
  } finally { s.close(); }
});
