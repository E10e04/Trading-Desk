import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Async, DataTable, Empty, ErrorState, Loading, Page, Panel, Segmented } from '../components/ui';
import { FreshPill } from '../components/Market';
import { useApi, useDebounced, usePersisted } from '../lib/hooks';
import { useMarket } from '../lib/market';
import { useQuotes } from '../lib/live';
import { CLASS_LABEL, price } from '../lib/format';

export default function Markets() {
  const nav = useNavigate();
  const { instruments, loaded, error, setSymbol, reload } = useMarket();
  const summary = useApi<{ classes: Record<string, Record<string, number>>; health: any }>('/api/markets/summary', 15000);
  const [text, setText] = useState('');
  const [cls, setCls] = usePersisted('markets.cls', '');
  const [fresh, setFresh] = usePersisted<'all' | 'live' | 'stale' | 'closed'>('markets.fresh', 'all');
  const [shown, setShown] = useState(100);
  const q = useDebounced(text, 150);
  const rows = useMemo(() => {
    const t = q.trim().toLowerCase();
    return instruments.filter((i) => (!cls || i.asset_class === cls) && (!t || i.symbol.toLowerCase().includes(t) || (i.description ?? '').toLowerCase().includes(t)) && (fresh === 'all' || (i.quote?.freshness ?? 'unknown') === fresh));
  }, [instruments, q, cls, fresh]);
  const page = rows.slice(0, shown);
  const quotes = useQuotes(page.map((i) => i.symbol));
  return (
    <Page title="Marchés" subtitle="Tous les instruments publiés par BiQuote, avec l’état de fraîcheur de chaque cotation.">
      <Async state={summary}>
        {(s) => (
          <div className="chips">
            <button className="chip" aria-pressed={cls === ''} onClick={() => setCls('')}>Tous · {instruments.length}</button>
            {Object.entries(s.classes).map(([c, d]) => (
              <button key={c} className="chip" aria-pressed={cls === c} onClick={() => setCls(c)} title={`${d.live} en direct, ${d.stale} périmées, ${d.closed} fermées`}>{CLASS_LABEL[c] ?? c} · {d.total} <span className="up">({d.live})</span></button>
            ))}
          </div>
        )}
      </Async>
      <Panel flush title={`${rows.length} marché(s)`} actions={<>
        <input className="input" style={{ width: 200 }} placeholder="Rechercher" aria-label="Rechercher" value={text} onChange={(e) => setText(e.target.value)} />
        <Segmented label="Fraîcheur" value={fresh} onChange={setFresh} options={[{ value: 'all', label: 'Tous' }, { value: 'live', label: 'En direct' }, { value: 'stale', label: 'Périmés' }, { value: 'closed', label: 'Fermés' }]} />
      </>}>
        {!loaded ? <Loading /> : error && !instruments.length ? <ErrorState message={error} onRetry={reload} /> : (
          <>
            <DataTable rows={page} rowKey={(r) => r.symbol} onRow={(r) => { setSymbol(r.symbol); nav(`/instrument/${r.symbol}`); }} empty={<Empty title="Aucun marché" hint={instruments.length ? 'Modifiez la recherche ou les filtres.' : 'Le catalogue est vide : BiQuote n’a pas encore répondu.'} />} cols={[
              { key: 's', header: 'Symbole', sort: (r) => r.symbol, render: (r) => <span className="sym">{r.symbol}</span> },
              { key: 'd', header: 'Description', hideSm: true, render: (r) => <span className="muted">{r.description}</span> },
              { key: 'c', header: 'Classe', hideSm: true, render: (r) => CLASS_LABEL[r.asset_class] ?? r.asset_class },
              { key: 'b', header: 'Bid', num: true, hideSm: true, render: (r) => price((quotes.get(r.symbol) ?? r.quote)?.bid, r.digits) },
              { key: 'a', header: 'Ask', num: true, hideSm: true, render: (r) => price((quotes.get(r.symbol) ?? r.quote)?.ask, r.digits) },
              { key: 'm', header: 'Prix', num: true, render: (r) => price((quotes.get(r.symbol) ?? r.quote)?.mid, r.digits) },
              { key: 'f', header: 'Donnée', render: (r) => { const qq = quotes.get(r.symbol) ?? r.quote; return <FreshPill freshness={qq?.freshness} ageS={qq?.age_s} />; } },
            ]} />
            {rows.length > shown && <div className="row" style={{ justifyContent: 'center', padding: 12 }}><button className="btn" onClick={() => setShown(shown + 200)}>Afficher plus ({rows.length - shown} restants)</button></div>}
          </>
        )}
      </Panel>
    </Page>
  );
}
