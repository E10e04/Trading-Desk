import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Async, Confirm, DataTable, Empty, Page, Panel, Pill, Tabs, useToast } from '../components/ui';
import { FreshPill, LivePrice } from '../components/Market';
import { PortfolioSelect } from '../components/PortfolioSelect';
import { api } from '../lib/api';
import { useApi } from '../lib/hooks';
import { useLiveEvent } from '../lib/live';
import { usePortfolios } from '../lib/portfolio';
import { dateTime, pct, qty, signed, tone, usd } from '../lib/format';

const LEG_ST: Record<string, string> = { open: 'en attente', triggered: 'déclenché', filled: 'exécuté', cancelled: 'annulé', rejected: 'rejeté', expired: 'expiré' };
const ORD_ST: Record<string, 'ok' | 'warn' | 'err' | 'mute' | 'info'> = { filled: 'ok', open: 'info', triggered: 'info', rejected: 'err', cancelled: 'mute', expired: 'mute' };

/** Détail de chaque ordre d'entrée avec ses deux jambes de protection (stop et take profit). */
function OrderDetails({ pid }: { pid: number }) {
  const nav = useNavigate();
  const st = useApi<{ items: any[] }>(`/api/orders?limit=500${pid > 0 ? `&portfolio_id=${pid}` : ''}`, 5000);
  useLiveEvent('fill', () => st.reload());
  const all = st.data?.items ?? [];
  const legs = (id: number, role: string) => all.find((o) => o.parent_id === id && o.role === role);
  const rows = all.filter((o) => o.role === 'standalone' || o.role === 'entry');
  const leg = (o: any, price: number | null | undefined) => (o ? <>{price ?? '—'} <span className="muted">({LEG_ST[o.status] ?? o.status})</span></> : '—');
  return (
    <Panel flush title={`${rows.length} ordre(s)`}>
      <Async state={st}>{() => <DataTable rows={rows} rowKey={(r) => r.id} onRow={(r) => nav(`/instrument/${r.symbol}`)} empty={<Empty title="Aucun ordre" hint="Les ordres des agents et vos ordres manuels apparaissent ici avec leur stop et leur take profit." />} cols={[
        { key: 'd', header: 'Date', hideSm: true, render: (r) => dateTime(r.created_at) },
        { key: 's', header: 'Instrument', render: (r) => <span className="sym">{r.symbol}</span> },
        { key: 'sd', header: 'Sens', render: (r) => (r.side === 'sell' ? <Pill kind="warn">Vente</Pill> : <Pill kind="info">Achat</Pill>) },
        { key: 'q', header: 'Quantité', num: true, render: (r) => qty(r.qty) },
        { key: 'e', header: 'Prix d’entrée', num: true, render: (r) => r.avg_fill_price ?? r.limit_price ?? 'marché' },
        { key: 'sl', header: 'Stop', num: true, render: (r) => leg(legs(r.id, 'stop_loss'), legs(r.id, 'stop_loss')?.stop_price) },
        { key: 'tp', header: 'Take profit', num: true, render: (r) => leg(legs(r.id, 'take_profit'), legs(r.id, 'take_profit')?.limit_price) },
        { key: 'st', header: 'Statut', render: (r) => <Pill kind={ORD_ST[r.status] ?? 'mute'}>{LEG_ST[r.status] ?? r.status}</Pill> },
        { key: 'so', header: 'Origine', hideSm: true, render: (r) => (r.source === 'manual' ? 'manuel' : r.source) },
      ]} />}</Async>
    </Panel>
  );
}

export default function Positions() {
  const [tab, setTab] = useState<'pos' | 'orders'>('pos');
  const nav = useNavigate();
  const toast = useToast();
  const pf = usePortfolios(0, true);
  const q = pf.sel > 0 ? `?portfolio_id=${pf.sel}` : '';
  const pos = useApi<{ items: any[] }>(`/api/positions${q}`, 3000);
  useLiveEvent('fill', () => pos.reload());
  const [close, setClose] = useState<any>(null);
  const [all, setAll] = useState(false);
  const closeOne = async (p: any) => {
    try {
      const o = await api<any>(`/api/positions/${p.portfolio_id}/${p.symbol}/close`, { method: 'POST', body: {} });
      toast(o.status === 'filled' ? 'success' : 'warning', `${p.symbol} : ${o.status}`, o.reject_reason ?? undefined);
    } catch (e) { toast('error', `Clôture de ${p.symbol} impossible`, (e as Error).message); }
    pos.reload();
  };
  const items = pos.data?.items ?? [];
  const total = items.reduce((a, p) => a + (p.unrealized_pnl ?? 0), 0);
  return (
    <Page title="Positions" subtitle="Positions papier ouvertes, valorisées au bid courant. Une cotation non fraîche est signalée et n’est jamais estimée."
      actions={<><PortfolioSelect items={pf.items} value={pf.sel} onChange={pf.setSel} all /><button className="btn danger" disabled={!items.length} onClick={() => setAll(true)}>Tout clôturer</button></>}>
      <Tabs value={tab} onChange={setTab} tabs={[{ id: 'pos', label: 'Positions ouvertes' }, { id: 'orders', label: 'Détail des ordres' }]} />
      {tab === 'orders' && <OrderDetails pid={pf.sel} />}
      {tab === 'pos' && <Panel flush title={`${items.length} position(s)`} actions={<span className={tone(total)}>P&L latent total {signed(total)} $</span>}>
        <Async state={pos}>{() => <DataTable rows={items} rowKey={(r) => r.id} onRow={(r) => nav(`/instrument/${r.symbol}`)} empty={<Empty title="Aucune position ouverte" hint="Les positions apparaissent dès qu’un ordre est exécuté." />} cols={[
          { key: 's', header: 'Instrument', render: (r) => <span className="sym">{r.symbol}</span> },
          { key: 'p', header: 'Portefeuille', hideSm: true, render: (r) => r.portfolio },
          { key: 'q', header: 'Quantité', num: true, render: (r) => <>{qty(Math.abs(r.qty))}{r.side === 'short' && <Pill kind="warn">Vente</Pill>}</> },
          { key: 'a', header: 'Prix moyen', num: true, render: (r) => r.avg_price },
          { key: 'm', header: 'Marché', num: true, render: (r) => <LivePrice symbol={r.symbol} /> },
          { key: 'u', header: 'P&L latent', num: true, render: (r) => <span className={tone(r.unrealized_pnl)}>{signed(r.unrealized_pnl)}</span> },
          { key: 'pc', header: '%', num: true, hideSm: true, render: (r) => <span className={tone(r.unrealized_pct)}>{pct(r.unrealized_pct)}</span> },
          { key: 'n', header: 'Notionnel', num: true, hideSm: true, render: (r) => usd(r.notional_usd, 0) },
          { key: 'sl', header: 'Stop / Cible', num: true, hideSm: true, render: (r) => (r.plan?.stop ? `${r.plan.stop} / ${r.plan.target}` : '—') },
          { key: 'o', header: 'Ouverte', hideSm: true, render: (r) => dateTime(r.opened_at) },
          { key: 'f', header: 'Donnée', hideSm: true, render: (r) => <FreshPill freshness={r.data_freshness} ageS={r.data_age_s} /> },
          { key: 'c', header: '', render: (r) => <button className="btn sm" onClick={(e) => { e.stopPropagation(); setClose(r); }}>Clôturer</button> },
        ]} />}</Async>
      </Panel>}
      {close && <Confirm title={`Clôturer ${close.symbol} ?`} confirmLabel="Clôturer au marché" danger message={close.side === 'short' ? `Rachat papier de ${qty(Math.abs(close.qty))} ${close.symbol} au marché, au prix ask courant avec glissement.` : `Vente papier de ${qty(close.qty)} ${close.symbol} au marché, au prix bid courant avec glissement.`} onConfirm={() => closeOne(close)} onClose={() => setClose(null)} />}
      {all && <Confirm title="Tout clôturer ?" confirmLabel="Tout clôturer" danger message={`${items.length} position(s) papier seront clôturées au marché.`} onConfirm={async () => { for (const p of items) await closeOne(p); }} onClose={() => setAll(false)} />}
    </Page>
  );
}
