import { useEffect, useMemo, useState } from 'react';
import { NavLink, Outlet } from 'react-router-dom';
import { Icons, Logo } from './icons';
import { Confirm, Pill, useToast } from './ui';
import { MarketSwitcher } from './Market';
import { api } from '../lib/api';
import { useApi } from '../lib/hooks';
import { live, useConnState, useLiveEvent, useQuotes } from '../lib/live';
import { useMarket } from '../lib/market';
import { price } from '../lib/format';
import { useTheme, type ThemeMode } from '../lib/theme';

export const NAV: { group: string; items: { to: string; label: string; icon: keyof typeof Icons }[] }[] = [
  { group: 'Pilotage', items: [
    { to: '/', label: 'Vue d’ensemble', icon: 'overview' },
    { to: '/markets', label: 'Marchés', icon: 'markets' },
    { to: '/instrument', label: 'Instrument', icon: 'instrument' },
    { to: '/charts', label: 'Graphiques', icon: 'charts' },
  ] },
  { group: 'Agents', items: [
    { to: '/agents', label: 'Agents IA', icon: 'agents' },
    { to: '/simulator', label: 'Simulateur', icon: 'simulator' },
    { to: '/strategies', label: 'Stratégies', icon: 'strategies' },
  ] },
  { group: 'Trading papier', items: [
    { to: '/orders', label: 'Ordres', icon: 'orders' },
    { to: '/positions', label: 'Positions', icon: 'positions' },
    { to: '/portfolios', label: 'Portefeuilles', icon: 'portfolios' },
  ] },
  { group: 'Suivi', items: [
    { to: '/analyses', label: 'Analyses et journal', icon: 'journal' },
    { to: '/notifications', label: 'Notifications', icon: 'bell' },
    { to: '/diagnostics', label: 'Diagnostic', icon: 'diagnostics' },
    { to: '/settings', label: 'Réglages', icon: 'settings' },
  ] },
];

const MODES: { v: ThemeMode; label: string; icon: keyof typeof Icons }[] = [
  { v: 'light', label: 'Clair', icon: 'sun' },
  { v: 'dark', label: 'Sombre', icon: 'moon' },
  { v: 'system', label: 'Système', icon: 'monitor' },
];
const PREFERRED = ['EURUSD', 'GBPUSD', 'USDJPY', 'XAUUSD', 'BTCUSD', 'US500', 'NAS100', 'USOIL'];

function ConnPill() {
  const ws = useConnState();
  const feed = useApi<{ stream: { state: string }; live: number; quotes: number }>('/api/feed/status', 10000);
  const st = feed.data?.stream?.state;
  const kind = ws !== 'open' ? 'err' : st === 'connected' ? 'ok' : 'warn';
  const label = ws !== 'open' ? 'Interface déconnectée' : st === 'connected' ? `BiQuote · ${feed.data?.live ?? 0} en direct` : st ? `BiQuote : ${st}` : 'BiQuote : inconnu';
  return <Pill kind={kind}>{label}</Pill>;
}

function Ticker() {
  const { instruments, setSymbol } = useMarket();
  const syms = useMemo(() => {
    const have = new Set(instruments.map((i) => i.symbol));
    const pick = PREFERRED.filter((s) => have.has(s));
    return (pick.length ? pick : instruments.filter((i) => i.quote?.freshness === 'live').slice(0, 6).map((i) => i.symbol)).slice(0, 8);
  }, [instruments]);
  const quotes = useQuotes(syms);
  if (!syms.length) return <div className="ticker" />;
  return (
    <div className="ticker" aria-label="Cotations">
      {syms.map((s) => {
        const q = quotes.get(s);
        return (
          <button key={s} className="t" onClick={() => setSymbol(s)} title={q ? q.freshness : 'pas de cotation'}>
            <b>{s}</b>
            <span className={q?.freshness === 'live' ? '' : 'muted'}>{q ? price(q.mid, q.digits) : '—'}</span>
          </button>
        );
      })}
    </div>
  );
}

export function Layout() {
  const { mode, setMode } = useTheme();
  const toast = useToast();
  const pipeline = useApi<{ kill_switch: boolean; auto_execute: boolean; pipeline_enabled: boolean }>('/api/pipeline/status', 5000);
  const unread = useApi<{ unread: number }>('/api/notifications?unread=true&limit=1', 20000);
  const [confirm, setConfirm] = useState(false);
  const kill = !!pipeline.data?.kill_switch;

  useEffect(() => live.start(), []);
  useLiveEvent('notification', (n: { level: string; title: string; body?: string }) => {
    unread.reload();
    if (n.level === 'error' || n.level === 'warning' || n.level === 'success') toast(n.level as 'error', n.title, n.body);
  });

  const toggleKill = async (on: boolean) => {
    try {
      await api('/api/settings', { method: 'PUT', body: { kill_switch: on } });
      toast(on ? 'warning' : 'success', on ? 'Interrupteur d’arrêt activé' : 'Interrupteur d’arrêt désactivé', on ? 'Plus aucune position automatique ne sera prise.' : 'Les agents peuvent de nouveau prendre des positions papier.');
      pipeline.reload();
    } catch (e) {
      toast('error', 'Action impossible', (e as Error).message);
    }
  };

  return (
    <div className="app">
      <aside className="rail">
        <div className="brand">
          <Logo />
          <div>
            Trading Desk
            <small>Paper trading</small>
          </div>
        </div>
        <nav className="nav" aria-label="Navigation principale">
          {NAV.map((g) => (
            <div key={g.group}>
              <div className="nav-group">{g.group}</div>
              {g.items.map((i) => (
                <NavLink key={i.to} to={i.to} end={i.to === '/'} title={i.label}>
                  {Icons[i.icon]}
                  <span>{i.label}</span>
                  {i.to === '/notifications' && !!unread.data?.unread && <b className="badge">{unread.data.unread}</b>}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
      </aside>
      <header className="topbar">
        <MarketSwitcher />
        <Ticker />
        <span className="hide-sm"><ConnPill /></span>
        <Pill kind="info" plain>Papier</Pill>
        <button className={`btn ${kill ? 'danger' : ''}`} onClick={() => (kill ? void toggleKill(false) : setConfirm(true))} aria-pressed={kill} title="Interrupteur d’arrêt global : coupe toute prise de position automatique">
          {Icons.stop}
          <span className="hide-sm">{kill ? 'Arrêt actif — réactiver' : 'Arrêt d’urgence'}</span>
        </button>
        <div className="seg" role="group" aria-label="Thème">
          {MODES.map((m) => (
            <button key={m.v} aria-pressed={mode === m.v} title={m.label} aria-label={m.label} onClick={() => setMode(m.v)} style={{ padding: '0 8px' }}>
              <span style={{ display: 'inline-flex', width: 15, height: 15 }}>{Icons[m.icon]}</span>
            </button>
          ))}
        </div>
      </header>
      <main className="main" id="main">
        {kill && <div className="banner warn" role="status"><b>Interrupteur d’arrêt actif.</b> Les agents analysent mais ne prennent aucune position.</div>}
        <Outlet />
      </main>
      {confirm && (
        <Confirm title="Activer l’interrupteur d’arrêt ?" danger confirmLabel="Activer l’arrêt" message="Aucune nouvelle position automatique ne sera ouverte tant que l’interrupteur est actif. Les positions ouvertes et leurs stops/cibles ne sont pas modifiés." onConfirm={() => toggleKill(true)} onClose={() => setConfirm(false)} />
      )}
    </div>
  );
}
