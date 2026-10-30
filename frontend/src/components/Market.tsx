import { useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Icons } from './icons';
import { Pill } from './ui';
import { CLASS_LABEL, FRESH_LABEL, price } from '../lib/format';
import { useQuote, useQuotes } from '../lib/live';
import { useMarket, type Instrument } from '../lib/market';
import { useDebounced } from '../lib/hooks';

export function FreshPill({ freshness, ageS }: { freshness?: string; ageS?: number | null }) {
  const f = freshness ?? 'unknown';
  const kind = f === 'live' ? 'ok' : f === 'stale' ? 'warn' : f === 'closed' ? 'mute' : 'mute';
  return <Pill kind={kind}>{FRESH_LABEL[f] ?? f}{f === 'stale' && ageS ? ` · ${Math.round(ageS)} s` : ''}</Pill>;
}

export function LivePrice({ symbol, digits }: { symbol: string; digits?: number | null }) {
  const q = useQuote(symbol);
  if (!q) return <span className="muted">—</span>;
  return <span className={q.freshness === 'live' ? '' : 'muted'} title={FRESH_LABEL[q.freshness]}>{price(q.mid, digits ?? q.digits)}</span>;
}

/** Sélecteur de marché : recherche, filtre par classe, état de fraîcheur, navigation clavier. */
export function MarketSwitcher() {
  const { symbol, setSymbol, instruments, loaded, error, byId } = useMarket();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState('');
  const [cls, setCls] = useState('');
  const [idx, setIdx] = useState(0);
  const q = useDebounced(text, 120);
  const nav = useNavigate();
  const loc = useLocation();
  const ref = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const list = useMemo(() => {
    const t = q.trim().toLowerCase();
    return instruments.filter((i) => (!cls || i.asset_class === cls) && (!t || i.symbol.toLowerCase().includes(t) || (i.description ?? '').toLowerCase().includes(t))).slice(0, 120);
  }, [instruments, q, cls]);
  const classes = useMemo(() => [...new Set(instruments.map((i) => i.asset_class))].sort(), [instruments]);
  const quotes = useQuotes(open ? list.slice(0, 40).map((i) => i.symbol) : []);

  useEffect(() => {
    if (!open) return;
    input.current?.focus();
    const on = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && setOpen(false);
    document.addEventListener('mousedown', on);
    return () => document.removeEventListener('mousedown', on);
  }, [open]);
  useEffect(() => setIdx(0), [q, cls]);

  const choose = (i: Instrument) => {
    setSymbol(i.symbol);
    setOpen(false);
    setText('');
    const m = loc.pathname.match(/^\/(instrument|charts)\//);
    if (m) nav(`/${m[1]}/${i.symbol}`);
  };
  const cur = byId(symbol);
  return (
    <div className="rel" ref={ref}>
      <button className="btn" aria-haspopup="listbox" aria-expanded={open} onClick={() => setOpen(!open)} title="Changer de marché">
        <b>{symbol}</b>
        <span className="muted hide-sm">{cur ? CLASS_LABEL[cur.asset_class] ?? cur.asset_class : ''}</span>
        {Icons.chevron}
      </button>
      {open && (
        <div className="menu" onKeyDown={(e) => {
          if (e.key === 'Escape') setOpen(false);
          if (e.key === 'ArrowDown') { e.preventDefault(); setIdx((i) => Math.min(list.length - 1, i + 1)); }
          if (e.key === 'ArrowUp') { e.preventDefault(); setIdx((i) => Math.max(0, i - 1)); }
          if (e.key === 'Enter' && list[idx]) choose(list[idx]);
        }}>
          <div className="head stack" style={{ gap: 8 }}>
            <input ref={input} className="input" placeholder={`Rechercher parmi ${instruments.length} marchés`} aria-label="Rechercher un marché" value={text} onChange={(e) => setText(e.target.value)} />
            <div className="chips">
              <button className="chip" aria-pressed={cls === ''} onClick={() => setCls('')}>Tous</button>
              {classes.map((c) => <button key={c} className="chip" aria-pressed={cls === c} onClick={() => setCls(c)}>{CLASS_LABEL[c] ?? c}</button>)}
            </div>
          </div>
          <div className="switcher-list" role="listbox" aria-label="Marchés">
            {error && !instruments.length && <div className="state error">{error}</div>}
            {!loaded && <div className="state"><span className="spinner" /></div>}
            {loaded && !error && !list.length && <div className="state"><strong>Aucun marché</strong><span>{instruments.length ? 'Aucun résultat pour cette recherche.' : 'Le catalogue BiQuote n’est pas encore chargé.'}</span></div>}
            {list.map((i, n) => {
              const qq = quotes.get(i.symbol) ?? i.quote ?? undefined;
              return (
                <button key={i.symbol} className="switcher-item" role="option" aria-selected={n === idx} onClick={() => choose(i)}>
                  <b style={{ minWidth: 76 }}>{i.symbol}</b>
                  <span className="d">{i.description || CLASS_LABEL[i.asset_class]}</span>
                  <span className="num" style={{ minWidth: 70 }}>{qq ? price(qq.mid, i.digits) : '—'}</span>
                  <span className={`pill plain ${qq?.freshness === 'live' ? 'ok' : qq?.freshness === 'stale' ? 'warn' : 'mute'}`} style={{ width: 9, height: 9, padding: 0 }} title={FRESH_LABEL[qq?.freshness ?? 'unknown']} />
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
