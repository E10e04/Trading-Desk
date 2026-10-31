import { useEffect, useId, useMemo, useState } from 'react';
import { api } from '../lib/api';

/** Pastille d'instrument : couleurs des grandes cryptos, sinon teinte stable déduite du symbole. */
const COIN: Record<string, string> = { BTC: '#f7931a', ETH: '#627eea', SOL: '#9945ff', XRP: '#3d4a5c', ADA: '#2a6df4', DOGE: '#c2a633', LTC: '#7d8bb0', DOT: '#e6007a', LINK: '#2a5ada', AVAX: '#e84142', BCH: '#8dc351', XLM: '#2f7fd9', ATOM: '#4a5176', XAU: '#d4a62a', XAG: '#a8b0b8', EUR: '#3b6fd8', USD: '#3f9e68', GBP: '#c0485b', JPY: '#d8465a' };
const hue = (s: string) => [...s].reduce((a, c) => (a * 31 + c.charCodeAt(0)) % 360, 7);
export function Avatar({ symbol, size = 32 }: { symbol: string; size?: number }) {
  const base = symbol.replace(/USDT?$|USD$/, '') || symbol;
  const key = COIN[base.slice(0, 3)] ? base.slice(0, 3) : base;
  const bg = COIN[key] ?? `hsl(${hue(symbol)} 45% 42%)`;
  const label = base.length <= 4 ? base.slice(0, 3) : base.slice(0, 2);
  return <span className="avatar" style={{ width: size, height: size, background: bg, fontSize: size * 0.34 }} aria-hidden="true">{label}</span>;
}

/** Variation en %, colorée (vert / rouge) ; « — » si la source n'en donne pas. */
export function Change({ value, digits = 2 }: { value: number | null | undefined; digits?: number }) {
  if (value === null || value === undefined || !Number.isFinite(value)) return <span className="muted">—</span>;
  const cls = value > 0 ? 'up' : value < 0 ? 'down' : 'flat';
  return <span className={`chg ${cls}`}>{value > 0 ? '+' : value < 0 ? '−' : ''}{Math.abs(value).toFixed(digits)} %</span>;
}

/** Mini-courbe SVG (aucune donnée inventée : le composant ne dessine que les valeurs reçues). */
export function SparkLine({ values, width = 96, height = 32, up }: { values: number[]; width?: number; height?: number; up?: boolean }) {
  const id = useId();
  const pts = useMemo(() => {
    if (values.length < 2) return null;
    const lo = Math.min(...values), hi = Math.max(...values), span = hi - lo || 1;
    return values.map((v, i) => [(i / (values.length - 1)) * width, height - 2 - ((v - lo) / span) * (height - 4)] as const);
  }, [values, width, height]);
  if (!pts) return <svg width={width} height={height} aria-hidden="true" />;
  const rising = up ?? values[values.length - 1] >= values[0];
  const color = rising ? 'var(--success)' : 'var(--error)';
  const line = pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true" className="spark">
      <defs><linearGradient id={id} x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor={color} stopOpacity="0.28" /><stop offset="1" stopColor={color} stopOpacity="0" /></linearGradient></defs>
      <polygon points={`0,${height} ${line} ${width},${height}`} fill={`url(#${id})`} />
      <polyline points={line} fill="none" stroke={color} strokeWidth="1.6" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

// Historique court par instrument : cache 90 s et au plus 3 requêtes à la fois (ménage les limites des fournisseurs).
const cache = new Map<string, { at: number; v: number[] }>();
const waiting: (() => void)[] = [];
let busy = 0;
async function fetchSpark(symbol: string): Promise<number[]> {
  const hit = cache.get(symbol);
  if (hit && Date.now() - hit.at < 90_000) return hit.v;
  if (busy >= 3) await new Promise<void>((r) => waiting.push(r));
  busy++;
  try {
    const r = await api<{ bars: { c: number }[] }>(`/api/bars/${encodeURIComponent(symbol)}?tf=5m&limit=60`);
    const v = r.bars.map((b) => b.c);
    cache.set(symbol, { at: Date.now(), v });
    return v;
  } finally {
    busy--;
    waiting.shift()?.();
  }
}
export function useSpark(symbol: string): number[] {
  const [v, setV] = useState<number[]>(() => cache.get(symbol)?.v ?? []);
  useEffect(() => {
    let dead = false;
    fetchSpark(symbol).then((x) => !dead && setV(x)).catch(() => {});
    return () => { dead = true; };
  }, [symbol]);
  return v;
}
export function Spark({ symbol, width, height }: { symbol: string; width?: number; height?: number }) {
  return <SparkLine values={useSpark(symbol)} width={width} height={height} />;
}
