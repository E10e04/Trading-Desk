import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api';

export interface ApiState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

/** Charge une ressource ; `pollMs` rafraîchit sans écran de chargement. Les erreurs sont conservées (jamais de données inventées). */
export function useApi<T = any>(path: string | null, pollMs = 0): ApiState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(!!path);
  const seq = useRef(0);
  const load = useCallback(
    async (silent: boolean) => {
      if (!path) return;
      const n = ++seq.current;
      if (!silent) setLoading(true);
      try {
        const d = await api<T>(path);
        if (n === seq.current) {
          setData(d);
          setError(null);
        }
      } catch (e) {
        if (n === seq.current && (e as Error).name !== 'AbortError') setError((e as Error).message);
      } finally {
        if (n === seq.current) setLoading(false);
      }
    },
    [path],
  );
  useEffect(() => {
    setData(null);
    setError(null);
    void load(false);
    if (!pollMs || !path) return;
    const id = window.setInterval(() => void load(true), pollMs);
    return () => clearInterval(id);
  }, [load, pollMs, path]);
  return { data, error, loading, reload: () => void load(true) };
}

export function useDebounced<T>(value: T, ms = 250): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function usePersisted<T>(key: string, initial: T): [T, (v: T) => void] {
  const [v, setV] = useState<T>(() => {
    try {
      const s = localStorage.getItem(`td.${key}`);
      return s ? (JSON.parse(s) as T) : initial;
    } catch {
      return initial;
    }
  });
  const set = (x: T) => {
    setV(x);
    try {
      localStorage.setItem(`td.${key}`, JSON.stringify(x));
    } catch {
      /* ignore */
    }
  };
  return [v, set];
}
