// Client HTTP : jeton local (localStorage ou /api/session en loopback). Aucune clé de service n'est jamais manipulée ici.
const KEY = 'td.token';

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

let token: string | null = null;
try {
  token = localStorage.getItem(KEY);
} catch {
  /* stockage indisponible */
}
const listeners = new Set<() => void>();

export function getToken(): string | null {
  return token;
}
export function setToken(t: string | null): void {
  token = t;
  try {
    if (t) localStorage.setItem(KEY, t);
    else localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
  listeners.forEach((l) => l());
}
export function onTokenChange(cb: () => void): () => void {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

/** Récupère le jeton local si le backend est sur cette machine. */
export async function bootstrapToken(): Promise<boolean> {
  if (token) return true;
  try {
    const r = await fetch('/api/session');
    if (r.ok) {
      setToken((await r.json()).token);
      return true;
    }
  } catch {
    /* backend injoignable */
  }
  return false;
}

function message(detail: unknown, fallback: string): string {
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object') {
    const d = detail as { reasons?: string[]; msg?: string }[] | { reasons?: string[] };
    if (Array.isArray(d)) return d.map((x) => x.msg ?? JSON.stringify(x)).join('; ');
    if (Array.isArray((d as { reasons?: string[] }).reasons)) return (d as { reasons: string[] }).reasons.join('; ');
  }
  return fallback;
}

export async function api<T = any>(path: string, opts: { method?: string; body?: unknown; signal?: AbortSignal } = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method: opts.method ?? (opts.body !== undefined ? 'POST' : 'GET'),
      headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: opts.signal,
    });
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e;
    throw new ApiError(0, 'Serveur injoignable. Vérifiez que le backend est démarré.');
  }
  if (res.status === 401) {
    setToken(null);
    throw new ApiError(401, 'Session expirée : saisissez de nouveau le jeton.');
  }
  const text = await res.text();
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) throw new ApiError(res.status, message(data?.detail, `Erreur ${res.status}`), data?.detail);
  return data as T;
}
