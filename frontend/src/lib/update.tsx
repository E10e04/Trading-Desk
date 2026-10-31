import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react';
import { api } from './api';

export interface Asset { name: string; url: string; size?: number; sha256?: string | null; platform: 'win' | 'mac' | null }
export interface Release { version: string; tag: string; name: string; published_at?: string; notes: string; url: string; assets: Asset[] }
export interface UpdateInfo { current: string; latest: Release | null; releases: Release[]; update_available: boolean; error: string | null; repo: string }

declare global {
  interface Window {
    desktop?: { isDesktop: true; platform: string; installUpdate: (a: Asset) => Promise<unknown>; onUpdateProgress: (cb: (p: number) => void) => () => void };
  }
}

interface Ctx {
  info: UpdateInfo | null;
  open: boolean;           // fenêtre de mise à jour affichée
  setOpen: (v: boolean) => void;
  pending: boolean;        // mise à jour disponible mais reportée : icône dans la barre latérale
  checking: boolean;
  check: (force?: boolean) => Promise<void>;
  install: () => Promise<void>;
  installing: boolean;
  progress: number | null;
  error: string | null;
}
const C = createContext<Ctx>({ info: null, open: false, setOpen: () => {}, pending: false, checking: false, check: async () => {}, install: async () => {}, installing: false, progress: null, error: null });
export const useUpdate = () => useContext(C);

/** Compare la version installée à la dernière release à CHAQUE ouverture de l'application ; ouvre la fenêtre si elles diffèrent. */
export function UpdateProvider({ children }: { children: ReactNode }) {
  const [info, setInfo] = useState<UpdateInfo | null>(null);
  const [open, setOpen] = useState(false);
  const [checking, setChecking] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const check = useCallback(async (force = false) => {
    setChecking(true);
    try {
      const r = await api<UpdateInfo>(`/api/update/check${force ? '?force=true' : ''}`);
      setInfo(r);
      return;
    } catch {
      /* hors ligne : jamais bloquant */
    } finally {
      setChecking(false);
    }
  }, []);

  useEffect(() => {
    let alive = true;
    void (async () => {
      setChecking(true);
      try {
        const r = await api<UpdateInfo>('/api/update/check?force=true');
        if (!alive) return;
        setInfo(r);
        if (r.update_available) setOpen(true);
      } catch { /* silencieux */ } finally { if (alive) setChecking(false); }
    })();
    return () => { alive = false; };
  }, []);

  useEffect(() => window.desktop?.onUpdateProgress((p) => setProgress(p)), []);

  const install = useCallback(async () => {
    const latest = info?.latest;
    if (!latest) return;
    setError(null);
    if (!window.desktop) { window.open(latest.url, '_blank', 'noopener'); return; } // mode web : page de la release
    const kind = window.desktop.platform === 'win32' ? 'win' : window.desktop.platform === 'darwin' ? 'mac' : null;
    const asset = latest.assets.find((a) => a.platform === kind);
    if (!asset) { setError('Aucun installateur pour votre système dans cette version. Ouvrez la page de la release.'); return; }
    setInstalling(true); setProgress(0);
    try { await window.desktop.installUpdate(asset); } catch (e) { setError((e as Error).message.replace(/^Error invoking remote method '[^']+': (Error: )?/, '')); setInstalling(false); setProgress(null); }
  }, [info]);

  return <C.Provider value={{ info, open, setOpen, pending: !!info?.update_available && !open, checking, check, install, installing, progress, error }}>{children}</C.Provider>;
}
