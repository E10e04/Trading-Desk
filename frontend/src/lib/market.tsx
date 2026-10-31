import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { api } from './api';
import { live, type Quote } from './live';

export interface Instrument {
  symbol: string; description: string; asset_class: string; base_ccy?: string; quote_ccy?: string; digits: number | null; exchange?: string; profile?: string; qty_step?: number;
  quote: Quote | null;
}
interface Ctx { symbol: string; setSymbol: (s: string) => void; instruments: Instrument[]; loaded: boolean; error: string | null; byId: (s: string) => Instrument | undefined; reload: () => void }
const MarketCtx = createContext<Ctx>({ symbol: 'EURUSD', setSymbol: () => {}, instruments: [], loaded: false, error: null, byId: () => undefined, reload: () => {} });
export const useMarket = () => useContext(MarketCtx);

export function MarketProvider({ children }: { children: ReactNode }) {
  const [symbol, setSym] = useState<string>(() => {
    try { return localStorage.getItem('td.symbol') || 'EURUSD'; } catch { return 'EURUSD'; }
  });
  const [instruments, setInstruments] = useState<Instrument[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    api<{ items: Instrument[] }>('/api/instruments?limit=2000')
      .then((r) => {
        setInstruments(r.items);
        r.items.forEach((i) => i.quote && live.seed(i.quote));
        setError(null);
        setLoaded(true);
      })
      .catch((e: Error) => {
        setError(e.message);
        setLoaded(true);
      });
  }, []);
  useEffect(() => {
    load();
    const id = window.setInterval(load, 120000);
    // le serveur annonce « catalogue prêt » : on recharge aussitôt au lieu d'attendre le prochain cycle
    const off = live.on('catalogue', () => load());
    return () => { clearInterval(id); off(); };
  }, [load]);
  // catalogue vide (serveur en cours de démarrage) : nouvel essai toutes les 3 s
  useEffect(() => {
    if (instruments.length > 0) return;
    const id = window.setInterval(load, 3000);
    return () => clearInterval(id);
  }, [instruments.length, load]);
  const setSymbol = useCallback((s: string) => {
    setSym(s);
    try { localStorage.setItem('td.symbol', s); } catch { /* ignore */ }
  }, []);
  const index = useMemo(() => new Map(instruments.map((i) => [i.symbol, i] as const)), [instruments]);
  const byId = useCallback((s: string) => index.get(s), [index]);
  return <MarketCtx.Provider value={{ symbol, setSymbol, instruments, loaded, error, byId, reload: load }}>{children}</MarketCtx.Provider>;
}
