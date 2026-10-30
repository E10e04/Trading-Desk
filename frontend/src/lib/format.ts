export function num(x: number | null | undefined, digits = 2): string {
  if (x === null || x === undefined || !Number.isFinite(x)) return '—';
  return x.toLocaleString('fr-FR', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}
export function usd(x: number | null | undefined, digits = 2): string {
  if (x === null || x === undefined || !Number.isFinite(x)) return '—';
  return `${num(x, digits)} $`;
}
export function signed(x: number | null | undefined, digits = 2): string {
  if (x === null || x === undefined || !Number.isFinite(x)) return '—';
  return `${x > 0 ? '+' : ''}${num(x, digits)}`;
}
export function pct(x: number | null | undefined, digits = 2): string {
  if (x === null || x === undefined || !Number.isFinite(x)) return '—';
  return `${signed(x, digits)} %`;
}
export function price(x: number | null | undefined, digits?: number | null): string {
  if (x === null || x === undefined || !Number.isFinite(x)) return '—';
  const d = digits ?? (Math.abs(x) >= 1000 ? 2 : Math.abs(x) >= 10 ? 3 : 5);
  return x.toLocaleString('fr-FR', { minimumFractionDigits: d, maximumFractionDigits: d, useGrouping: false });
}
export function qty(x: number | null | undefined): string {
  if (x === null || x === undefined || !Number.isFinite(x)) return '—';
  return x.toLocaleString('fr-FR', { maximumFractionDigits: 8 });
}
export function dateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString('fr-FR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
}
export function timeOnly(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}
export function age(s: number | null | undefined): string {
  if (s === null || s === undefined) return '—';
  if (s < 90) return `${Math.round(s)} s`;
  if (s < 5400) return `${Math.round(s / 60)} min`;
  if (s < 172800) return `${Math.round(s / 3600)} h`;
  return `${Math.round(s / 86400)} j`;
}
export function ms(x: number | null | undefined): string {
  if (x === null || x === undefined) return '—';
  return x < 1000 ? `${x} ms` : `${(x / 1000).toFixed(1)} s`;
}
export const CLASS_LABEL: Record<string, string> = { forex: 'Forex', metal: 'Métaux', energy: 'Énergie', crypto: 'Crypto', index: 'Indices', equity: 'Actions', other: 'Autres' };
export const FRESH_LABEL: Record<string, string> = { live: 'En direct', stale: 'Périmée', closed: 'Marché fermé', unknown: 'Inconnue' };
export const tone = (x: number | null | undefined): 'up' | 'down' | 'flat' => (x === null || x === undefined || x === 0 ? 'flat' : x > 0 ? 'up' : 'down');
