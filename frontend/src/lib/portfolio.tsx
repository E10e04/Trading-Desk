import { useEffect } from 'react';
import { useApi, usePersisted } from './hooks';

export interface Portfolio {
  id: number; name: string; active: boolean; initial_capital: number; balance: number; equity: number; unrealized_pnl: number; realized_pnl: number; fees_paid: number;
  margin_used: number; free_margin: number; leverage: number; exposure_usd: number; exposure_pct: number | null; commission_bps: number; slippage_bps: number;
  risk: Record<string, any>; return_pct: number; positions: any[]; open_orders: number; valuation_incomplete?: boolean; created_at: string;
}
/** Portefeuilles + sélection persistée (« 0 » = tous). */
export function usePortfolios(pollMs = 5000, allowAll = false) {
  const st = useApi<{ items: Portfolio[] }>('/api/portfolios', pollMs);
  const [sel, setSel] = usePersisted<number>('portfolio', allowAll ? 0 : -1);
  const items = st.data?.items ?? [];
  useEffect(() => {
    if (!items.length) return;
    if (sel > 0 && items.some((p) => p.id === sel)) return;
    if (sel === 0 && allowAll) return;
    setSel(items[0].id);
  }, [items.length, sel]); // eslint-disable-line react-hooks/exhaustive-deps
  return { ...st, items, sel, setSel, current: items.find((p) => p.id === sel) };
}
