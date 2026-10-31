'use strict';
const { app, BrowserWindow, Menu, dialog, shell, ipcMain } = require('electron');
const path = require('node:path');
const os = require('node:os');
const { startBackend } = require('./backend');
const { installUpdate } = require('./updater');

let backend = null;
let win = null;

ipcMain.handle('update:install', async (event, asset) => {
  // uniquement depuis notre propre interface
  if (!backend || !event.senderFrame.url.startsWith(backend.url)) throw new Error('appel refusé');
  return installUpdate(asset, {
    tmpDir: path.join(os.tmpdir(), 'trading-desk-update'),
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
    dialog.showErrorBox('Trading Desk — démarrage impossible', String(e.message).slice(0, 1500));
    app.quit();
    return;
  }
  win = new BrowserWindow({
    width: 1440, height: 900, minWidth: 900, minHeight: 600, backgroundColor: '#1E1E1E', title: 'Trading Desk',
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true, preload: path.join(__dirname, 'preload.js') },
  });
  // Le jeton local est servi par /api/session (loopback uniquement) : l'interface le récupère toute seule.
  win.webContents.setWindowOpenHandler(({ url }) => { shell.openExternal(url); return { action: 'deny' }; });
  win.webContents.on('will-navigate', (e, url) => { if (!url.startsWith(backend.url)) { e.preventDefault(); shell.openExternal(url); } });
  await win.loadURL(backend.url);
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    ...(process.platform === 'darwin' ? [{ role: 'appMenu' }] : []),
    { role: 'editMenu' }, { role: 'viewMenu' }, { role: 'windowMenu' },
  ]));
}

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => { if (win) { if (win.isMinimized()) win.restore(); win.focus(); } });
  app.whenReady().then(boot);
  app.on('window-all-closed', () => app.quit());
  let closing = false;
  app.on('before-quit', (e) => {
    if (backend && !closing) { closing = true; e.preventDefault(); backend.stop().finally(() => app.quit()); }
  });
}
