import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Async, Banner, DataTable, Empty, KV, Kpi, Page, Panel, Pill, Segmented, useToast } from '../components/ui';
import { Chart, TFS, type PriceLine, type TF } from '../components/Chart';
import { FreshPill } from '../components/Market';
import { OrderModal } from '../components/OrderModal';
import { api } from '../lib/api';
import { useApi, usePersisted } from '../lib/hooks';
import { useMarket } from '../lib/market';
import { useQuote } from '../lib/live';
import { CLASS_LABEL, dateTime, price, qty, signed, tone } from '../lib/format';

export default function Instrument() {
  const params = useParams();
  const nav = useNavigate();
  const toast = useToast();
  const { symbol: current, setSymbol } = useMarket();
  const symbol = (params.symbol ?? current).toUpperCase();
  useEffect(() => { if (params.symbol) setSymbol(params.symbol.toUpperCase()); }, [params.symbol]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (!params.symbol) nav(`/instrument/${symbol}`, { replace: true }); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const [tf, setTf] = usePersisted<TF>('tf', '5m');
  const [order, setOrder] = useState(false);
  const [busy, setBusy] = useState(false);
  const info = useApi<any>(`/api/instruments/${encodeURIComponent(symbol)}`, 10000);
  const runs = useApi<{ items: any[] }>(`/api/runs?instrument=${encodeURIComponent(symbol)}&limit=10`, 10000);
  const watch = useApi<{ items: { symbol: string }[] }>('/api/watchlist', 0);
  const q = useQuote(symbol);
  const d = info.data;
  const watched = !!watch.data?.items.some((w) => w.symbol === symbol);
  const lines: PriceLine[] = (d?.positions ?? []).flatMap((p: any) => [
    { price: p.avg_price, title: 'Entrée', kind: 'entry' as const },
    ...(p.plan?.stop ? [{ price: p.plan.stop, title: 'Stop', kind: 'stop' as const }] : []),
    ...(p.plan?.target ? [{ price: p.plan.target, title: 'Cible', kind: 'target' as const }] : []),
  ]);
  const evaluate = async () => {
    setBusy(true);
    try {
      const r = await api<{ triggered: boolean; proposals: any[] }>('/api/pipeline/evaluate', { body: { symbol } });
      toast(r.triggered ? 'success' : 'info', r.triggered ? 'Signal détecté' : 'Aucun signal', r.triggered ? 'La chaîne Risk → Broker a été exécutée.' : 'Les conditions d’entrée ne sont pas réunies maintenant.');
      runs.reload();
    } catch (e) { toast('error', 'Évaluation impossible', (e as Error).message); } finally { setBusy(false); }
  };
  const toggleWatch = async () => {
    try {
      if (watched) await api(`/api/watchlist/${symbol}`, { method: 'DELETE' });
      else await api('/api/watchlist', { body: { symbol, reason: 'ajout manuel' } });
      watch.reload();
    } catch (e) { toast('error', 'Liste de surveillance', (e as Error).message); }
  };
  return (
    <Page title={symbol} subtitle={d ? `${d.description || ''} ${d.asset_class ? '· ' + (CLASS_LABEL[d.asset_class] ?? d.asset_class) : ''}` : undefined}
      actions={<><button className="btn" onClick={toggleWatch}>{watched ? 'Retirer de la surveillance' : 'Surveiller'}</button><button className="btn" onClick={evaluate} disabled={busy}>Évaluer maintenant</button><button className="btn primary" onClick={() => setOrder(true)}>Ordre papier</button></>}>
      {info.error && !d && <Banner kind="err">{info.error}</Banner>}
      <div className="grid c4 keep2">
        <Kpi label="Prix" value={q ? price(q.mid, q.digits ?? d?.digits) : '—'} sub={q ? <FreshPill freshness={q.freshness} ageS={q.age_s} /> : 'Aucune cotation'} />
        <Kpi label="Bid / Ask" value={q ? `${price(q.bid, q.digits)} / ${price(q.ask, q.digits)}` : '—'} />
        <Kpi label="Spread" value={q ? price(q.spread, q.digits) : '—'} sub={q?.spread && q.mid ? `${((q.spread / q.mid) * 1e4).toFixed(1)} pb` : undefined} />
        <Kpi label="État du marché" value={q?.market_state ?? '—'} sub={q ? `Reçu ${dateTime(q.recv)}` : undefined} />
      </div>
      <Panel title="Graphique" actions={<><Segmented label="Période" value={tf} onChange={setTf} options={TFS.map((t) => ({ value: t, label: t }))} /><button className="btn sm" onClick={() => nav(`/charts/${symbol}`)}>Plein écran</button></>}>
        <Chart symbol={symbol} tf={tf} lines={lines} digits={d?.digits} height={400} />
      </Panel>
      <div className="grid c2">
        <Panel title="Caractéristiques"><Async state={info}>{(x) => <KV rows={[['Description', x.description], ['Classe', CLASS_LABEL[x.asset_class] ?? x.asset_class], ['Devise de base', x.base_ccy], ['Devise de cotation', x.quote_ccy], ['Décimales', x.digits], ['Pas de quantité', x.qty_step], ['Place', x.exchange], ['Profil de session', x.profile], ['Fraîcheur', <FreshPill key="f" freshness={x.freshness?.status} ageS={x.freshness?.age_s} />]]} />}</Async></Panel>
        <Panel title="Positions sur cet instrument" flush>
          <DataTable rows={d?.positions ?? []} rowKey={(r: any) => r.id} empty={<Empty title="Aucune position" />} cols={[
            { key: 'p', header: 'Portefeuille', render: (r: any) => r.portfolio },
            { key: 'q', header: 'Quantité', num: true, render: (r: any) => qty(r.qty) },
            { key: 'a', header: 'Prix moyen', num: true, render: (r: any) => r.avg_price },
            { key: 'u', header: 'P&L', num: true, render: (r: any) => <span className={tone(r.unrealized_pnl)}>{signed(r.unrealized_pnl)}</span> },
          ]} />
        </Panel>
      </div>
      <Panel title="Activité des agents sur cet instrument" flush>
        <Async state={runs}>{(r) => <DataTable rows={r.items} rowKey={(x) => x.id} empty={<Empty title="Aucune exécution pour cet instrument" />} cols={[
          { key: 't', header: 'Heure', render: (x) => dateTime(x.started_at) },
          { key: 'a', header: 'Agent', render: (x) => x.agent },
          { key: 's', header: 'Statut', render: (x) => <Pill kind={x.status === 'ok' ? 'ok' : x.status === 'error' ? 'err' : 'warn'}>{x.status}</Pill> },
          { key: 'r', header: 'Résumé', render: (x) => <span className="muted">{x.summary}</span> },
        ]} />}</Async>
      </Panel>
      {order && <OrderModal symbol={symbol} onClose={() => setOrder(false)} onDone={() => info.reload()} />}
    </Page>
  );
}
