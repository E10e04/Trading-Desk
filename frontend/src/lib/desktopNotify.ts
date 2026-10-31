// Notifications du système : apparaissent sur le bureau même quand l'application est réduite, masquée ou en arrière-plan.
export interface Notif { level?: string; title: string; body?: string }

const KEY = 'td.notif.desktop';
export function desktopNotifEnabled(): boolean {
  try { return localStorage.getItem(KEY) !== 'false'; } catch { return true; }
}
export function setDesktopNotifEnabled(v: boolean): void {
  try { localStorage.setItem(KEY, String(v)); } catch { /* ignore */ }
}
export function webNotifSupported(): boolean {
  return typeof window !== 'undefined' && 'Notification' in window;
}
export function webNotifPermission(): NotificationPermission | 'unsupported' {
  return webNotifSupported() ? Notification.permission : 'unsupported';
}
export async function requestWebNotifPermission(): Promise<NotificationPermission | 'unsupported'> {
  if (!webNotifSupported()) return 'unsupported';
  try { return await Notification.requestPermission(); } catch { return Notification.permission; }
}

/** Affiche une notification système. `force` : même si la fenêtre est au premier plan (bouton « Tester »). */
export async function sendSystemNotification(n: Notif, opts: { force?: boolean; route?: string } = {}): Promise<boolean> {
  if (!opts.force && !desktopNotifEnabled()) return false;
  const away = typeof document !== 'undefined' && (document.hidden || !document.hasFocus());
  if (!opts.force && !away) return false; // au premier plan, la bulle dans l'application suffit
  const route = opts.route ?? '/notifications';
  try {
    if (window.desktop?.notify) return await window.desktop.notify({ title: n.title, body: n.body, route, silent: n.level === 'info' });
    if (webNotifPermission() === 'granted') {
      const w = new Notification(n.title, { body: n.body, tag: 'monkora' });
      w.onclick = () => { window.focus(); window.location.hash = `#${route}`; w.close(); };
      return true;
    }
  } catch { /* la notification est un confort : jamais bloquant */ }
  return false;
}

/** Regroupe les rafales : plusieurs alertes en moins de 1,5 s donnent une seule notification de synthèse. */
export function createCoalescer(send: (n: Notif) => void, wait = 1500) {
  let queue: Notif[] = [];
  let timer: ReturnType<typeof setTimeout> | null = null;
  const flush = () => {
    timer = null;
    const q = queue;
    queue = [];
    if (!q.length) return;
    send(q.length === 1 ? q[0] : { level: 'info', title: `${q.length} nouvelles notifications`, body: q.slice(0, 3).map((x) => x.title).join(' · ') + (q.length > 3 ? ' …' : '') });
  };
  return {
    push(n: Notif) { queue.push(n); if (!timer) timer = setTimeout(flush, wait); },
    flush,
    dispose() { if (timer) clearTimeout(timer); timer = null; queue = []; },
  };
}
