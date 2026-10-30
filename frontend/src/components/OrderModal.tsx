import { useState } from 'react';
import { Field, Modal, useToast, Banner } from './ui';
import { api } from '../lib/api';
import { usePortfolios } from '../lib/portfolio';
import { useMarket } from '../lib/market';
import { useQuote } from '../lib/live';
import { price } from '../lib/format';

/** Ordre MANUEL papier. Validé par le moteur (marge, exposition, pas de vente à découvert). */
export function OrderModal({ symbol: initial, onClose, onDone }: { symbol?: string; onClose: () => void; onDone?: () => void }) {
  const toast = useToast();
  const { symbol: current, instruments } = useMarket();
  const pf = usePortfolios(0);
  const [symbol, setSymbol] = useState((initial ?? current).toUpperCase());
  const [side, setSide] = useState<'buy' | 'sell'>('buy');
  const [type, setType] = useState('market');
  const [qty, setQty] = useState('');
  const [limit, setLimit] = useState('');
  const [stopP, setStopP] = useState('');
  const [sl, setSl] = useState('');
  const [tp, setTp] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const q = useQuote(symbol);
  const inst = instruments.find((i) => i.symbol === symbol);
  const n = (v: string) => (v.trim() === '' ? null : Number(v.replace(',', '.')));
  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api('/api/orders', { body: { portfolio_id: pf.sel, symbol, side, type, qty: n(qty), limit_price: type.includes('limit') ? n(limit) : null, stop_price: type.startsWith('stop') ? n(stopP) : null, tif: 'gtc', stop_loss: n(sl), take_profit: n(tp) } });
      toast('success', 'Ordre papier envoyé', `${side === 'buy' ? 'Achat' : 'Vente'} ${qty} ${symbol}`);
      onDone?.();
      onClose();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const valid = !!n(qty) && (n(qty) as number) > 0 && pf.sel > 0 && (!type.includes('limit') || n(limit)) && (!type.startsWith('stop') || n(stopP));
  return (
    <Modal title="Nouvel ordre papier" onClose={onClose} footer={<><button className="btn" onClick={onClose}>Annuler</button><button className="btn primary" disabled={!valid || busy} onClick={submit}>Envoyer l’ordre</button></>}>
      <div className="stack">
        <Banner kind="info">Paper trading : aucun argent réel. Les ordres manuels passent par les contrôles du moteur (marge, exposition, vente à découvert interdite).</Banner>
        {err && <Banner kind="err">{err}</Banner>}
        <div className="form-grid">
          <Field label="Portefeuille">{(id) => <select id={id} className="input" value={pf.sel} onChange={(e) => pf.setSel(Number(e.target.value))}>{pf.items.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select>}</Field>
          <Field label="Instrument">{(id) => <input id={id} className="input" list="syms" value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} />}</Field>
          <Field label="Sens">{(id) => <select id={id} className="input" value={side} onChange={(e) => setSide(e.target.value as 'buy')}><option value="buy">Achat</option><option value="sell">Vente (clôture)</option></select>}</Field>
          <Field label="Type">{(id) => <select id={id} className="input" value={type} onChange={(e) => setType(e.target.value)}><option value="market">Marché</option><option value="limit">Limite</option><option value="stop">Stop</option><option value="stop_limit">Stop-limite</option></select>}</Field>
          <Field label="Quantité" hint={inst?.qty_step ? `Pas : ${inst.qty_step}` : undefined}>{(id) => <input id={id} className="input" inputMode="decimal" value={qty} onChange={(e) => setQty(e.target.value)} />}</Field>
          {type.includes('limit') && <Field label="Prix limite">{(id) => <input id={id} className="input" inputMode="decimal" value={limit} onChange={(e) => setLimit(e.target.value)} />}</Field>}
          {type.startsWith('stop') && <Field label="Prix de déclenchement">{(id) => <input id={id} className="input" inputMode="decimal" value={stopP} onChange={(e) => setStopP(e.target.value)} />}</Field>}
          <Field label="Stop-loss (option)">{(id) => <input id={id} className="input" inputMode="decimal" value={sl} onChange={(e) => setSl(e.target.value)} />}</Field>
          <Field label="Take-profit (option)">{(id) => <input id={id} className="input" inputMode="decimal" value={tp} onChange={(e) => setTp(e.target.value)} />}</Field>
        </div>
        <datalist id="syms">{instruments.slice(0, 500).map((i) => <option key={i.symbol} value={i.symbol} />)}</datalist>
        <div className="muted">{q ? <>Bid {price(q.bid, q.digits)} · Ask {price(q.ask, q.digits)} · Spread {price(q.spread, q.digits)} · {q.freshness === 'live' ? 'données en direct' : 'données non fraîches : le moteur refusera l’exécution'}</> : 'Aucune cotation reçue pour cet instrument.'}</div>
      </div>
    </Modal>
  );
}
