import { useMemo } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Async, DataTable, Empty, Page, Panel, Pill } from '../components/ui';
import { Icons } from '../components/icons';
import { Chart, TFS, type TF } from '../components/Chart';
import { PortfolioSelect } from '../components/PortfolioSelect';
import { FreshPill, LivePrice } from '../components/Market';
import { Avatar, Change, Spark, SparkLine } from '../components/Visual';
import { useApi, usePersisted } from '../lib/hooks';
import { usePortfolios } from '../lib/portfolio';
import { useMarket } from '../lib/market';
import { useLiveEvent, useQuote, useQuotes } from '../lib/live';
import { CLASS_LABEL, dateTime, ms, price, qty, signed, tone, usd } from '../lib/format';

const STATUS: Record<string, 'ok' | 'warn' | 'err' | 'mute'> = { ok: 'ok', partial: 'warn', skipped: 'mute', error: 'err', executed: 'ok', pending: 'warn', blocked: 'err', rejected: 'err', expired: 'mute', failed: 'err' };
const HEADLINE = ['BTCUSD', 'ETHUSD', 'EURUSD', 'XAUUSD', 'SOLUSD', 'GBPUSD', 'US500', 'NAS100'];
const TAB_ORDER = ['crypto', 'forex', 'metal', 'index', 'equity', 'energy', 'other'];

function MarketCard({ symbol, onOpen }: { symbol: string; onOpen: () => void }) {
  const { byId } = useMarket();
  const q = useQuote(symbol);
  const inst = byId(symbol);
  return (
    <button className="panel mcard" onClick={onOpen} title={q ? undefined : 'pas de cotation'}>
      <span className="mc-head"><Avatar symbol={symbol} size={34} /><span><b>{symbol}</b><small className="muted">{inst?.description || CLASS_LABEL[inst?.asset_class ?? ''] || ''}</small></span></span>
      <span className="mc-body">
        <span className="mc-price"><b className={q?.freshness === 'live' ? '' : 'muted'}>{q ? price(q.mid, inst?.digits ?? q.digits) : '—'}</b><Change value={q?.chg} /></span>
        <Spark symbol={symbol} width={92} height={34} />
      </span>
    </button>
  );
}

function MainChart({ symbol }: { symbol: string }) {
  const { byId } = useMarket();
  const q = useQuote(symbol);
  const [tf, setTf] = usePersisted<TF>('overview.tf', '5m');
  const inst = byId(symbol);
  return (
    <Panel className="chart-panel" title={<span className="row" style={{ gap: 10 }}><Avatar symbol={symbol} size={30} /><span className="ch-title"><b>{symbol}</b><small className="muted">{inst?.description}</small></span><span className="ch-price">{q ? price(q.mid, inst?.digits ?? q.digits) : '—'}</span><Change value={q?.chg} /></span>}
      actions={<><div className="pills" role="group" aria-label="Unité de temps">{TFS.map((t) => <button key={t} aria-pressed={tf === t} onClick={() => setTf(t)}>{t}</button>)}</div><Link className="btn sm" to={`/instrument/${symbol}`}>Ouvrir</Link></>}>
      <Chart symbol={symbol} tf={tf} height={340} digits={inst?.digits} />
    </Panel>
  );
}

function PortfolioCard({ p, eq }: { p: any; eq: number[] }) {
  const { symbol } = useMarket();
  return (
    <section className="panel pcard">
      <div className="pc-top"><span className="muted">Equity du portefeuille papier</span><Pill kind="info" plain>USD</Pill></div>
      <div className="pc-value">{usd(p.equity)}</div>
      <div className="pc-sub"><Change value={p.return_pct} /> <span className={tone(p.realized_pnl + p.unrealized_pnl)}>{signed(p.realized_pnl + p.unrealized_pnl)} $</span> <span className="muted">depuis le début</span></div>
      <div className="pc-spark"><SparkLine values={eq} width={300} height={54} /></div>
      <div className="pc-grid">
        <div><small className="muted">P&amp;L latent</small><b className={tone(p.unrealized_pnl)}>{usd(p.unrealized_pnl)}</b></div>
        <div><small className="muted">Marge libre</small><b>{usd(p.free_margin)}</b></div>
        <div><small className="muted">Exposition</small><b>{p.exposure_pct ?? '—'} %</b></div>
        <div><small className="muted">Positions</small><b>{p.positions.length}</b></div>
      </div>
      <div className="quick">
        <Link className="qa primary" to={`/instrument/${symbol}`}>{Icons.plus}<span>Ordre papier</span></Link>
        <Link className="qa" to="/positions">{Icons.positions}<span>Positions</span></Link>
        <Link className="qa" to="/agents">{Icons.agents}<span>Agents</span></Link>
        <Link className="qa" to="/notifications">{Icons.bell}<span>Alertes</span></Link>
      </div>
    </section>
  );
}

export default function Overview() {
  const nav = useNavigate();
  const { instruments, setSymbol, symbol } = useMarket();
  const pf = usePortfolios(5000);
  const pipe = useApi<any>('/api/pipeline/status', 5000);
  const props = useApi<{ items: any[] }>('/api/proposals?limit=8', 8000);
  const runs = useApi<{ items: any[] }>('/api/runs?limit=8', 8000);
  const fills = useApi<{ items: any[] }>('/api/fills?limit=6', 8000);
  const feed = useApi<any>('/api/feed/status', 10000);
  const eq = useApi<{ items: { ts: string; equity: number }[] }>(pf.current ? `/api/portfolios/${pf.current.id}/equity?limit=200` : null, 30000);
  const [tab, setTab] = usePersisted<string>('overview.tab', 'crypto');
  useLiveEvent('proposal', () => props.reload());
  useLiveEvent('agent_run', () => runs.reload());
  useLiveEvent('fill', () => fills.reload());
  const p = pf.current;

  const have = useMemo(() => new Set(instruments.map((i) => i.symbol)), [instruments]);
  const headline = useMemo(() => {
    const pick = HEADLINE.filter((s) => have.has(s));
    return (pick.length >= 4 ? pick : [...pick, ...instruments.filter((i) => i.quote?.freshness === 'live' && !pick.includes(i.symbol)).map((i) => i.symbol)]).slice(0, 4);
  }, [have, instruments]);
  const chartSymbol = have.has(symbol) ? symbol : headline[0] ?? symbol;
  const classes = useMemo(() => TAB_ORDER.filter((c) => instruments.some((i) => i.asset_class === c)), [instruments]);
  const active = classes.includes(tab) ? tab : classes[0];
  const rows = useMemo(() => instruments.filter((i) => i.asset_class === active).sort((a, b) => (a.quote?.freshness === 'live' ? 0 : 1) - (b.quote?.freshness === 'live' ? 0 : 1) || a.symbol.localeCompare(b.symbol)).slice(0, 8), [instruments, active]);
  const rowQuotes = useQuotes(rows.map((r) => r.symbol));
  const movers = useMemo(() => instruments.filter((i) => typeof i.quote?.chg === 'number' && i.quote.freshness === 'live').sort((a, b) => (b.quote!.chg as number) - (a.quote!.chg as number)).slice(0, 5), [instruments]);

  return (
    <Page title="Vue d’ensemble" subtitle="Marchés, portefeuille papier et décisions des agents. Aucune somme réelle n’est engagée." actions={pf.items.length > 0 && <PortfolioSelect items={pf.items} value={pf.sel} onChange={pf.setSel} />}>
      {pipe.data && (
        <div className="row">
          <Pill kind={pipe.data.pipeline_enabled ? 'ok' : 'warn'}>Pipeline {pipe.data.pipeline_enabled ? 'actif' : 'en pause'}</Pill>
          <Pill kind={pipe.data.auto_execute ? 'info' : 'mute'}>{pipe.data.auto_execute ? 'Exécution automatique (papier)' : 'Approbation manuelle'}</Pill>
          {pipe.data.kill_switch && <Pill kind="err">Interrupteur d’arrêt actif</Pill>}
          <span className="muted">Surveillance : {pipe.data.watchlist?.length ?? 0} instrument(s)</span>
        </div>
      )}
      {headline.length > 0 && <div className="grid c4 keep2">{headline.map((s) => <MarketCard key={s} symbol={s} onOpen={() => { setSymbol(s); nav(`/instrument/${s}`); }} />)}</div>}
      <Async state={pf}>
        {() => (
          <div className="grid side">
            {chartSymbol ? <MainChart symbol={chartSymbol} /> : <Panel><Empty title="Aucun marché chargé" hint="Le catalogue n’est pas encore disponible." /></Panel>}
            {!p ? (
              <Panel><Empty title="Aucun portefeuille papier" hint="Créez un portefeuille pour que les agents puissent prendre des positions simulées." action={<Link className="btn primary" to="/portfolios">Créer un portefeuille</Link>} /></Panel>
            ) : (
              <PortfolioCard p={p} eq={(eq.data?.items ?? []).map((x) => x.equity)} />
            )}
          </div>
        )}
      </Async>
      <div className="grid side">
        <Panel flush title="Marchés" actions={<div className="pills" role="tablist" aria-label="Classe d’actifs">{classes.map((c) => <button key={c} role="tab" aria-selected={active === c} aria-pressed={active === c} onClick={() => setTab(c)}>{CLASS_LABEL[c] ?? c}</button>)}</div>}>
          <DataTable rows={rows} rowKey={(r) => r.symbol} onRow={(r) => { setSymbol(r.symbol); nav(`/instrument/${r.symbol}`); }} empty={<Empty title="Aucun instrument" hint="Le catalogue est vide pour cette classe." />} cols={[
            { key: 's', header: 'Instrument', render: (r) => <span className="asset"><Avatar symbol={r.symbol} size={28} /><span><b>{r.symbol}</b><small className="muted">{r.description}</small></span></span> },
            { key: 'p', header: 'Prix', num: true, render: (r) => { const q = rowQuotes.get(r.symbol) ?? r.quote; return q ? price(q.mid, r.digits) : '—'; } },
            { key: 'c', header: '24 h', num: true, render: (r) => <Change value={(rowQuotes.get(r.symbol) ?? r.quote)?.chg} /> },
            { key: 'g', header: 'Tendance', hideSm: true, render: (r) => <Spark symbol={r.symbol} width={88} height={28} /> },
            { key: 'f', header: 'Donnée', hideSm: true, render: (r) => { const q = rowQuotes.get(r.symbol) ?? r.quote; return <FreshPill freshness={q?.freshness} ageS={q?.age_s} />; } },
            { key: 'a', header: '', num: true, render: (r) => <Link className="btn sm" to={`/instrument/${r.symbol}`} onClick={() => setSymbol(r.symbol)}>Trader</Link> },
          ]} />
        </Panel>
        <div className="stack">
          <Panel title="Plus fortes hausses" flush actions={<Link to="/markets">Tout voir</Link>}>
            {movers.length ? (
              <ul className="rows">{movers.map((m, i) => (
                <li key={m.symbol}><button onClick={() => { setSymbol(m.symbol); nav(`/instrument/${m.symbol}`); }}>
                  <span className="rk">{i + 1}</span><Avatar symbol={m.symbol} size={28} /><span className="grow"><b>{m.symbol}</b><small className="muted">{price(m.quote!.mid, m.digits)}</small></span><Change value={m.quote!.chg} />
                </button></li>
              ))}</ul>
            ) : <Empty title="Pas de variation disponible" hint="Les variations apparaissent dès que des marchés cotent en direct." />}
          </Panel>
          <Panel title="Dernières exécutions" flush actions={<Link to="/orders">Ordres</Link>}>
            <Async state={fills}>{(d) => d.items.length ? (
              <ul className="rows">{d.items.slice(0, 5).map((f) => (
                <li key={f.id}><span className="static"><span className={`dot ${f.side === 'buy' ? 'up' : 'down'}`} /><span className="grow"><b>{f.side === 'buy' ? 'Achat' : 'Vente'} {f.symbol}</b><small className="muted">{dateTime(f.ts)}</small></span><span className="num">{qty(f.qty)} @ {f.price}</span></span></li>
              ))}</ul>
            ) : <Empty title="Aucune exécution" hint="Les positions papier prises par les agents apparaîtront ici." />}</Async>
          </Panel>
        </div>
      </div>
      {p && (
        <Panel title="Positions ouvertes" flush actions={<Link to="/positions">Tout voir</Link>}>
          <DataTable rows={p.positions} rowKey={(r: any) => r.id} onRow={(r: any) => nav(`/instrument/${r.symbol}`)} empty={<Empty title="Aucune position ouverte" />} cols={[
            { key: 's', header: 'Instrument', render: (r: any) => <span className="asset"><Avatar symbol={r.symbol} size={26} /><b>{r.symbol}</b></span> },
            { key: 'q', header: 'Quantité', num: true, render: (r: any) => qty(r.qty) },
            { key: 'a', header: 'Prix moyen', num: true, render: (r: any) => r.avg_price },
            { key: 'm', header: 'Marché', num: true, render: (r: any) => <LivePrice symbol={r.symbol} /> },
            { key: 'p', header: 'P&L latent', num: true, render: (r: any) => <span className={tone(r.unrealized_pnl)}>{signed(r.unrealized_pnl)}</span> },
            { key: 'f', header: 'Donnée', hideSm: true, render: (r: any) => <FreshPill freshness={r.data_freshness} ageS={r.data_age_s} /> },
          ]} />
        </Panel>
      )}
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
      {feed.data?.catalogue_error && <p className="down">{feed.data.catalogue_error}</p>}
    </Page>
  );
}
