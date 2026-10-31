'use strict';
const { contextBridge, ipcRenderer } = require('electron');

// Seule surface exposée à l'interface : installer une mise à jour et suivre sa progression.
contextBridge.exposeInMainWorld('desktop', {
  isDesktop: true,
  platform: process.platform,
  installUpdate: (asset) => ipcRenderer.invoke('update:install', asset),
  onUpdateProgress: (cb) => {
    const h = (_e, p) => cb(p);
    ipcRenderer.on('update:progress', h);
    return () => ipcRenderer.removeListener('update:progress', h);
  },
});
