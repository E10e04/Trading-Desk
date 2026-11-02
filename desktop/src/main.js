'use strict';
const { app, BrowserWindow, Menu, Tray, Notification, dialog, shell, ipcMain, nativeImage } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { startBackend } = require('./backend');
const { installUpdate } = require('./updater');
const { createNotifier } = require('./notifier');

const APP_NAME = 'Monkora';
app.setName(APP_NAME);
// Identifiant inchangé : les notifications Windows et la mise à jour sur place restent rattachées à la même application.
if (process.platform === 'win32') app.setAppUserModelId('app.tradingdesk.desktop');
// Les données (base SQLite, clé OpenRouter) vivaient dans « Trading Desk » : on les garde au même endroit si ce dossier existe.
try {
  const base = app.getPath('appData');
  const old = ['Trading Desk', 'trading-desk-desktop'].map((n) => path.join(base, n)).find((d) => fs.existsSync(d));
  if (old && !fs.existsSync(path.join(base, APP_NAME))) app.setPath('userData', old);
} catch { /* on repart sur le dossier par défaut */ }

let backend = null;
let win = null;
let tray = null;
let quitting = false;
let hintShown = false;

function showWindow(route) {
  if (!win) return;
  if (win.isMinimized()) win.restore();
  win.show();
  win.focus();
  if (route) win.webContents.send('notify:open', route);
}

const notifier = createNotifier({ Notification, onClick: (route) => showWindow(route || '/notifications') });

ipcMain.handle('notify:show', (event, payload) => {
  if (!backend || !event.senderFrame.url.startsWith(backend.url)) throw new Error('appel refusé');
  return notifier.show(payload);
});

function makeTray() {
  const dir = path.join(__dirname, 'assets');
  const img = nativeImage.createFromPath(path.join(dir, 'tray.png'));
  if (img.isEmpty()) return;
  tray = new Tray(img);
  tray.setToolTip(APP_NAME);
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: `Ouvrir ${APP_NAME}`, click: () => showWindow() },
    { type: 'separator' },
    { label: 'Quitter', click: () => { quitting = true; app.quit(); } },
  ]));
  tray.on('click', () => showWindow());
}

ipcMain.handle('update:install', async (event, asset) => {
  // uniquement depuis notre propre interface
  if (!backend || !event.senderFrame.url.startsWith(backend.url)) throw new Error('appel refusé');
  return installUpdate(asset, {
    tmpDir: path.join(os.tmpdir(), 'monkora-update'),
    onProgress: (p) => event.sender.send('update:progress', p),
    openPath: (f) => shell.openPath(f),
    quit: () => setTimeout(() => app.quit(), 800),
  });
});

async function boot() {
  const dataDir = path.join(app.getPath('userData'), 'data');
  try {
    backend = await startBackend({ dataDir, resourcesPath: app.isPackaged ? process.resourcesPath : undefined, repoRoot: path.resolve(__dirname, '..', '..'), env: { ...process.env, APP_VERSION: app.getVersion() } });
  } catch (e) {
    dialog.showErrorBox('Monkora — démarrage impossible', String(e.message).slice(0, 1500));
    app.quit();
    return;
  }
  win = new BrowserWindow({
    width: 1440, height: 900, minWidth: 900, minHeight: 600, backgroundColor: '#1E1E1E', title: APP_NAME,
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true, backgroundThrottling: false, preload: path.join(__dirname, 'preload.js') },
  });
  // Le jeton local est servi par /api/session (loopback uniquement) : l'interface le récupère toute seule.
  win.webContents.setWindowOpenHandler(({ url }) => { shell.openExternal(url); return { action: 'deny' }; });
  win.webContents.on('will-navigate', (e, url) => { if (!url.startsWith(backend.url)) { e.preventDefault(); shell.openExternal(url); } });
  // Fermer la fenêtre garde l'application active dans la zone de notification : les alertes continuent d'arriver.
  win.on('close', (e) => {
    if (quitting) return;
    e.preventDefault();
    win.hide();
    if (!hintShown) {
      hintShown = true;
      notifier.show({ title: `${APP_NAME} reste actif`, body: 'Les alertes continuent d’arriver. Utilisez l’icône de la zone de notification pour rouvrir ou quitter.', silent: true });
    }
  });
  // Écran qui ne répond plus ou processus d'affichage tombé : on recharge l'interface au lieu de laisser une fenêtre vide.
  const reload = () => { if (win && !win.isDestroyed()) win.webContents.reloadIgnoringCache(); };
  win.webContents.on('render-process-gone', (_e, d) => { if (d.reason !== 'clean-exit') setTimeout(reload, 500); });
  win.webContents.on('did-fail-load', (_e, code, _desc, url, isMain) => { if (isMain && code !== -3 && url.startsWith(backend.url)) setTimeout(reload, 2000); });
  win.on('unresponsive', () => setTimeout(() => { if (win && !win.isDestroyed()) reload(); }, 8000));
  makeTray();
  await win.loadURL(backend.url);
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    ...(process.platform === 'darwin' ? [{ role: 'appMenu' }] : []),
    { role: 'editMenu' }, { role: 'viewMenu' }, { role: 'windowMenu' },
  ]));
}

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => showWindow());
  app.on('activate', () => showWindow());
  app.whenReady().then(boot);
  app.on('window-all-closed', () => { /* la fenêtre est seulement masquée : on reste actif */ });
  let closing = false;
  app.on('before-quit', (e) => {
    quitting = true;
    if (backend && !closing) { closing = true; e.preventDefault(); backend.stop().finally(() => app.quit()); }
  });
}
