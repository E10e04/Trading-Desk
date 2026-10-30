import type { Portfolio } from '../lib/portfolio';

export function PortfolioSelect({ items, value, onChange, all }: { items: Portfolio[]; value: number; onChange: (v: number) => void; all?: boolean }) {
  return (
    <select className="input" style={{ width: 'auto', minWidth: 170 }} aria-label="Portefeuille" value={value} onChange={(e) => onChange(Number(e.target.value))}>
      {all && <option value={0}>Tous les portefeuilles</option>}
      {items.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
    </select>
  );
}
