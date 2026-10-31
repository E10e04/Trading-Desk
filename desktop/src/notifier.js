'use strict';
// Notifications du bureau : affichées par le système même quand la fenêtre est réduite ou masquée.

const MAX_TITLE = 120;
const MAX_BODY = 400;

function clamp(v, n) {
  const s = typeof v === 'string' ? v : v == null ? '' : String(v);
  return s.replace(/[\u0000-\u001f]+/g, ' ').trim().slice(0, n);
}

/** `Notification` est injectée (celle d'Electron en production) pour pouvoir tester sans interface. */
function createNotifier({ Notification, onClick, keep = new Set() }) {
  return {
    supported: () => !!Notification && (typeof Notification.isSupported !== 'function' || Notification.isSupported()),
    show(payload) {
      if (!this.supported()) return false;
      const p = payload && typeof payload === 'object' ? payload : {};
      const title = clamp(p.title, MAX_TITLE) || 'Monkora';
      const n = new Notification({ title, body: clamp(p.body, MAX_BODY), silent: p.silent === true });
      keep.add(n); // évite la collecte par le ramasse-miettes avant le clic
      n.on('click', () => { keep.delete(n); onClick && onClick(clamp(p.route, 200)); });
      n.on('close', () => keep.delete(n));
      n.show();
      return true;
    },
  };
}

module.exports = { createNotifier, clamp };
