import { Link, useNavigate } from 'react-router-dom';
import { Async, DataTable, Empty, Kpi, Page, Panel, Pill } from '../components/ui';
import { EquityChart } from '../components/EquityChart';
import { PortfolioSelect } from '../components/PortfolioSelect';
import { FreshPill, LivePrice } from '../components/Market';
import { useApi } from '../lib/hooks';
import { usePortfolios } from '../lib/portfolio';
import { useLiveEvent } from '../lib/live';
import { dateTime, ms, pct, qty, signed, tone, usd } from '../lib/format';

const STATUS: Record<string, 'ok' | 'warn' | 'err' | 'mute'> = { ok: 'ok', partial: 'warn', skipped: 'mute', error: 'err', executed: 'ok', pending: 'warn', blocked: 'err', rejected: 'err', expired: 'mute', failed: 'err' };

export default function Overview() {
  const nav = useNavigate();
  const pf = usePortfolios(5000);
  const pipe = useApi<any>('/api/pipeline/status', 5000);
  const props = useApi<{ items: any[] }>('/api/proposals?limit=8', 8000);
  const runs = useApi<{ items: any[] }>('/api/runs?limit=8', 8000);
  const feed = useApi<any>('/api/feed/status', 10000);
  const eq = useApi<{ items: { ts: string; equity: number }[] }>(pf.current ? `/api/portfolios/${pf.current.id}/equity?limit=800` : null, 30000);
  useLiveEvent('proposal', () => props.reload());
  useLiveEvent('agent_run', () => runs.reload());
  const p = pf.current;
  return (
    <Page title="Vue d’ensemble" subtitle="Portefeuille papier, état des agents et dernières décisions. Aucune somme réelle n’est engagée." actions={pf.items.length > 0 && <PortfolioSelect items={pf.items} value={pf.sel} onChange={pf.setSel} />}>
      {pipe.data && (
        <div className="row">
          <Pill kind={pipe.data.pipeline_enabled ? 'ok' : 'warn'}>Pipeline {pipe.data.pipeline_enabled ? 'actif' : 'en pause'}</Pill>
          <Pill kind={pipe.data.auto_execute ? 'info' : 'mute'}>{pipe.data.auto_execute ? 'Exécution automatique (papier)' : 'Approbation manuelle'}</Pill>
          {pipe.data.kill_switch && <Pill kind="err">Interrupteur d’arrêt actif</Pill>}
          <span className="muted">Surveillance : {pipe.data.watchlist?.length ?? 0} instrument(s)</span>
        </div>
      )}
      <Async state={pf}>
        {() =>
          !p ? (
            <Panel><Empty title="Aucun portefeuille papier" hint="Créez un portefeuille pour que les agents puissent prendre des positions simulées." action={<Link className="btn primary" to="/portfolios">Créer un portefeuille</Link>} /></Panel>
          ) : (
            <>
              <div className="grid c4 keep2">
                <Kpi label="Equity" value={usd(p.equity)} sub={`Capital initial ${usd(p.initial_capital, 0)}`} />
                <Kpi label="Rendement" value={pct(p.return_pct)} tone={tone(p.return_pct)} sub={`Réalisé ${usd(p.realized_pnl)}`} />
                <Kpi label="P&L latent" value={usd(p.unrealized_pnl)} tone={tone(p.unrealized_pnl)} sub={p.valuation_incomplete ? 'valorisation incomplète (cotation manquante)' : `${p.positions.length} position(s)`} />
                <Kpi label="Marge libre" value={usd(p.free_margin)} sub={`Exposition ${p.exposure_pct ?? '—'} % · levier ${p.leverage}x`} />
              </div>
              <div className="grid side">
                <Panel title="Courbe d’equity"><Async state={eq}>{(d) => (d.items.length > 1 ? <EquityChart points={d.items} /> : <Empty title="Pas encore d’historique" hint="Un point est enregistré à intervalle régulier tant que le backend tourne." />)}</Async></Panel>
                <Panel title="Flux de marché">
                  {feed.data ? (
                    <div className="stack" style={{ gap: 8 }}>
                      <div className="row between"><span>Connexion</span><Pill kind={feed.data.stream?.state === 'connected' ? 'ok' : 'err'}>{feed.data.stream?.state ?? 'inconnue'}</Pill></div>
                      <div className="row between"><span>En direct</span><b className="num">{feed.data.live}</b></div>
                      <div className="row between"><span>Périmées</span><b className="num warn">{feed.data.stale}</b></div>
                      <div className="row between"><span>Marché fermé</span><b className="num">{feed.data.closed}</b></div>
                      <div className="row between"><span>Instruments</span><b className="num">{feed.data.instruments}</b></div>
                      {feed.data.catalogue_error && <span className="down">{feed.data.catalogue_error}</span>}
                    </div>
                  ) : <Empty title="Flux inconnu" />}
                </Panel>
              </div>
              <Panel title="Positions ouvertes" flush actions={<Link to="/positions">Tout voir</Link>}>
                <DataTable rows={p.positions} rowKey={(r) => r.id} onRow={(r) => nav(`/instrument/${r.symbol}`)} empty={<Empty title="Aucune position ouverte" />} cols={[
                  { key: 's', header: 'Instrument', render: (r) => <span className="sym">{r.symbol}</span> },
                  { key: 'q', header: 'Quantité', num: true, render: (r) => qty(r.qty) },
                  { key: 'a', header: 'Prix moyen', num: true, render: (r) => r.avg_price },
                  { key: 'm', header: 'Marché', num: true, render: (r) => <LivePrice symbol={r.symbol} /> },
                  { key: 'p', header: 'P&L latent', num: true, render: (r) => <span className={tone(r.unrealized_pnl)}>{signed(r.unrealized_pnl)}</span> },
                  { key: 'f', header: 'Donnée', hideSm: true, render: (r) => <FreshPill freshness={r.data_freshness} ageS={r.data_age_s} /> },
                ]} />
              </Panel>
            </>
          )
        }
      </Async>
      <div className="grid c2">
        <Panel title="Dernières propositions" flush actions={<Link to="/orders">Ordres</Link>}>
          <Async state={props}>{(d) => <DataTable rows={d.items} rowKey={(r) => r.id} empty={<Empty title="Aucune proposition" hint="Une proposition est créée à chaque signal d’entrée détecté." />} cols={[
            { key: 't', header: 'Heure', render: (r) => dateTime(r.ts) },
            { key: 's', header: 'Instrument', render: (r) => <span className="sym">{r.symbol}</span> },
            { key: 'r', header: 'Risk', render: (r) => (r.risk?.verdict ? <Pill kind={r.risk.verdict === 'PASS' ? 'ok' : 'err'}>{r.risk.verdict}</Pill> : '—') },
            { key: 'st', header: 'Statut', render: (r) => <Pill kind={STATUS[r.status] ?? 'mute'}>{r.status}</Pill> },
          ]} />}</Async>
        </Panel>
        <Panel title="Dernières exécutions d’agents" flush actions={<Link to="/agents">Agents</Link>}>
          <Async state={runs}>{(d) => <DataTable rows={d.items} rowKey={(r) => r.id} empty={<Empty title="Aucune exécution" />} cols={[
            { key: 't', header: 'Heure', render: (r) => dateTime(r.started_at) },
            { key: 'a', header: 'Agent', render: (r) => <Link to={`/agents/${r.agent}`}>{r.agent}</Link> },
            { key: 'i', header: 'Instrument', hideSm: true, render: (r) => r.instrument ?? '—' },
            { key: 'st', header: 'Statut', render: (r) => <Pill kind={STATUS[r.status] ?? 'mute'}>{r.status}</Pill> },
            { key: 'd', header: 'Durée', num: true, hideSm: true, render: (r) => ms(r.duration_ms) },
          ]} />}</Async>
        </Panel>
      </div>
    </Page>
  );
}
