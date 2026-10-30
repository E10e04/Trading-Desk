import { useEffect, useState } from 'react';

export type ThemeMode = 'light' | 'dark' | 'system';
const KEY = 'td.theme';

export function readMode(): ThemeMode {
  try {
    const v = localStorage.getItem(KEY);
    if (v === 'light' || v === 'dark' || v === 'system') return v;
  } catch {
    /* ignore */
  }
  return 'system';
}
export function resolve(mode: ThemeMode): 'light' | 'dark' {
  if (mode === 'system') return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  return mode;
}
export function applyTheme(mode: ThemeMode): 'light' | 'dark' {
  const r = resolve(mode);
  document.documentElement.dataset.theme = r;
  window.dispatchEvent(new CustomEvent('td-theme', { detail: r }));
  return r;
}
export function useTheme() {
  const [mode, setModeState] = useState<ThemeMode>(readMode);
  const [resolved, setResolved] = useState<'light' | 'dark'>(() => resolve(readMode()));
  useEffect(() => {
    setResolved(applyTheme(mode));
    try {
      localStorage.setItem(KEY, mode);
    } catch {
      /* ignore */
    }
    if (mode !== 'system') return;
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const on = () => setResolved(applyTheme('system'));
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  }, [mode]);
  return { mode, resolved, setMode: setModeState };
}
export function useResolvedTheme(): 'light' | 'dark' {
  const [t, setT] = useState<'light' | 'dark'>(() => (document.documentElement.dataset.theme === 'light' ? 'light' : 'dark'));
  useEffect(() => {
    const on = (e: Event) => setT((e as CustomEvent).detail);
    window.addEventListener('td-theme', on);
    return () => window.removeEventListener('td-theme', on);
  }, []);
  return t;
}
