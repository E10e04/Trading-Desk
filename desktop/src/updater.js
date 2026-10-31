'use strict';
// Téléchargement et lancement de l'installateur d'une nouvelle version, SANS désinstaller l'ancienne :
//   Windows : l'installateur NSIS (même appId) met à jour l'installation existante sur place.
//   macOS   : le DMG est ouvert ; il suffit de glisser l'application dans « Applications » en remplaçant l'ancienne.
// Sans dépendance Electron (testable avec Node seul). Sécurité : seules les URL de release GitHub du dépôt configuré sont acceptées,
// les redirections restent sur les domaines GitHub, et l'empreinte SHA-256 publiée par GitHub est vérifiée quand elle existe.
const https = require('node:https');
const http = require('node:http');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');

const REPO = process.env.UPDATE_REPO || 'E10e04/Trading-Desk';
const REDIRECT_HOSTS = [/^github\.com$/, /(^|\.)githubusercontent\.com$/];
const EXT = { win: '.exe', mac: '.dmg' };

function isAllowedAsset(url, repo = REPO) {
  try {
    const u = new URL(url);
    return u.protocol === 'https:' && u.hostname === 'github.com' && u.pathname.toLowerCase().startsWith(`/${repo.toLowerCase()}/releases/download/`);
  } catch { return false; }
}
const okRedirect = (u, insecure) => (insecure && u.hostname === '127.0.0.1') || (u.protocol === 'https:' && REDIRECT_HOSTS.some((r) => r.test(u.hostname)));

function download(url, dest, { onProgress, sha256, insecureForTests = false, maxRedirects = 5 } = {}) {
  return new Promise((resolve, reject) => {
    const go = (u, left) => {
      const target = new URL(u);
      if (!okRedirect(target, insecureForTests) && left !== maxRedirects) return reject(new Error(`redirection refusée vers ${target.hostname}`));
      const lib = target.protocol === 'http:' ? http : https;
      lib.get(target, { headers: { 'User-Agent': 'monkora-updater' } }, (res) => {
        if ([301, 302, 303, 307, 308].includes(res.statusCode) && res.headers.location) {
          res.resume();
          if (left <= 0) return reject(new Error('trop de redirections'));
          return go(new URL(res.headers.location, target).toString(), left - 1);
        }
        if (res.statusCode !== 200) { res.resume(); return reject(new Error(`téléchargement refusé (HTTP ${res.statusCode})`)); }
        const total = Number(res.headers['content-length'] || 0);
        let got = 0;
        const hash = crypto.createHash('sha256');
        const out = fs.createWriteStream(dest);
        res.on('data', (c) => { got += c.length; hash.update(c); if (onProgress && total) onProgress(got / total); });
        res.pipe(out);
        out.on('finish', () => {
          const digest = hash.digest('hex');
          if (sha256 && digest.toLowerCase() !== sha256.toLowerCase()) { fs.rmSync(dest, { force: true }); return reject(new Error('empreinte SHA-256 incorrecte : fichier rejeté')); }
          if (total && got !== total) { fs.rmSync(dest, { force: true }); return reject(new Error('téléchargement incomplet')); }
          resolve(dest);
        });
        out.on('error', reject);
        res.on('error', reject);
      }).on('error', reject);
    };
    go(url, maxRedirects);
  });
}

/** Télécharge puis lance l'installateur. `deps` permet de tout simuler dans les tests. */
async function installUpdate(asset, { platform = process.platform, tmpDir, repo = REPO, onProgress, openPath, quit, spawnImpl = spawn, insecureForTests = false } = {}) {
  const kind = platform === 'win32' ? 'win' : platform === 'darwin' ? 'mac' : null;
  if (!kind) throw new Error('mise à jour automatique non prise en charge sur ce système');
  if (!asset || (!isAllowedAsset(asset.url, repo) && !insecureForTests)) throw new Error('adresse de téléchargement non autorisée');
  const name = path.basename(String(asset.name || ''));
  if (!name.toLowerCase().endsWith(EXT[kind]) || name !== asset.name) throw new Error(`fichier inattendu (${EXT[kind]} attendu)`);
  fs.mkdirSync(tmpDir, { recursive: true });
  const dest = path.join(tmpDir, name);
  await download(asset.url, dest, { onProgress, sha256: asset.sha256 || undefined, insecureForTests });
  if (kind === 'win') {
    const child = spawnImpl(dest, [], { detached: true, stdio: 'ignore', windowsHide: false });
    child.unref?.();
  } else {
    const err = await openPath(dest);
    if (err) throw new Error(`ouverture du DMG impossible : ${err}`);
  }
  quit();
  return { file: dest, kind };
}

module.exports = { isAllowedAsset, download, installUpdate };
