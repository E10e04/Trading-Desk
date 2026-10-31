'use strict';
const { contextBridge, ipcRenderer } = require('electron');

// Seule surface exposée à l'interface : installer une mise à jour, afficher une notification système et recevoir le clic dessus.
contextBridge.exposeInMainWorld('desktop', {
  isDesktop: true,
  platform: process.platform,
  notify: (payload) => ipcRenderer.invoke('notify:show', payload),
  onNotificationOpen: (cb) => {
    const h = (_e, route) => cb(route);
    ipcRenderer.on('notify:open', h);
    return () => ipcRenderer.removeListener('notify:open', h);
  },
  installUpdate: (asset) => ipcRenderer.invoke('update:install', asset),
  onUpdateProgress: (cb) => {
    const h = (_e, p) => cb(p);
    ipcRenderer.on('update:progress', h);
    return () => ipcRenderer.removeListener('update:progress', h);
  },
});
