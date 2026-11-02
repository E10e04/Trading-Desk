import { useEffect, useState } from 'react';
import { Async, Banner, Confirm, DataTable, Empty, Field, KV, Kpi, Modal, Page, Panel, Pill, Switch, useToast } from '../components/ui';
import { EquityChart } from '../components/EquityChart';
import { api } from '../lib/api';
import { useApi } from '../lib/hooks';
import { usePortfolios, type Portfolio } from '../lib/portfolio';
import { num, pct, tone, usd } from '../lib/format';

const RISK_FIELDS: { k: string; label: string; hint?: string }[] = [
  { k: 'max_loss_per_trade_usd', label: 'Perte max par trade ($)' }, { k: 'max_daily_loss_usd', label: 'Perte max par jour ($)', hint: 'Pertes réalisées du jour (UTC)' },
  { k: 'max_order_usd', label: 'Ordre max ($)' }, { k: 'min_order_usd', label: 'Ordre min ($)' }, { k: 'max_position_pct', label: 'Position max (% du budget)' },
  { k: 'cash_buffer_pct', label: 'Réserve de marge (%)' }, { k: 'max_trades_per_day', label: 'Trades max par jour' }, { k: 'max_orders_per_day', label: 'Ordres max par jour' },
  { k: 'max_open_positions', label: 'Positions ouvertes max' }, { k: 'max_exposure_pct', label: 'Exposition brute max (% equity)' }, { k: 'max_spread_bps', label: 'Spread max (points de base)' },
  { k: 'min_reward_risk', label: 'R:R minimal' },
];

function Editor({ p, onClose, onSaved }: { p: Portfolio | null; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const [name, setName] = useState(p?.name ?? '');
  const [capital, setCapital] = useState(String(p?.initial_capital ?? 10000));
  const [lev, setLev] = useState(String(p?.leverage ?? 10));
  const [fee, setFee] = useState(String(p?.commission_bps ?? 1));
  const [slip, setSlip] = useState(String(p?.slippage_bps ?? 0.5));
  const [risk, setRisk] = useState<Record<string, any>>(p?.risk ?? {});
  const [err, setErr] = useState<string | null>(null);
  const n = (s: string) => Number(s.replace(',', '.'));
  const save = async () => {
    setErr(null);
    try {
      if (p) await api(`/api/portfolios/${p.id}`, { method: 'PATCH', body: { name, leverage: n(lev), commission_bps: n(fee), slippage_bps: n(slip), risk } });
      else await api('/api/portfolios', { body: { name, capital: n(capital), leverage: n(lev), commission_bps: n(fee), slippage_bps: n(slip) } });
      toast('success', p ? 'Portefeuille mis à jour' : 'Portefeuille créé');
      onSaved(); onClose();
    } catch (e) { setErr((e as Error).message); }
  };
  return (
    <Modal title={p ? `Modifier « ${p.name} »` : 'Nouveau portefeuille papier'} onClose={onClose} footer={<><button className="btn" onClick={onClose}>Annuler</button><button className="btn primary" disabled={!name.trim() || !(n(capital) > 0)} onClick={save}>Enregistrer</button></>}>
      <div className="stack">
        {err && <Banner kind="err">{err}</Banner>}
        <div className="form-grid">
          <Field label="Nom">{(id) => <input id={id} className="input" value={name} onChange={(e) => setName(e.target.value)} />}</Field>
          <Field label="Capital initial ($)" hint={p ? 'Modifiable uniquement via une réinitialisation' : undefined}>{(id) => <input id={id} className="input" inputMode="decimal" disabled={!!p} value={capital} onChange={(e) => setCapital(e.target.value)} />}</Field>
          <Field label="Levier">{(id) => <input id={id} className="input" inputMode="decimal" value={lev} onChange={(e) => setLev(e.target.value)} />}</Field>
          <Field label="Commission (pb)">{(id) => <input id={id} className="input" inputMode="decimal" value={fee} onChange={(e) => setFee(e.target.value)} />}</Field>
          <Field label="Glissement (pb)">{(id) => <input id={id} className="input" inputMode="decimal" value={slip} onChange={(e) => setSlip(e.target.value)} />}</Field>
        </div>
        {p && (
          <>
            <h3>Limites de risque</h3>
            <div className="form-grid">
              {RISK_FIELDS.map((f) => <Field key={f.k} label={f.label} hint={f.hint}>{(id) => <input id={id} className="input" inputMode="decimal" value={String(risk[f.k] ?? '')} onChange={(e) => { const v = e.target.value; setRisk({ ...risk, [f.k]: v === '' || Number.isNaN(n(v)) ? v : n(v) }); }} />}</Field>)}
              <Field label="Stop obligatoire">{() => <Switch checked={!!risk.require_stop} onChange={(v) => setRisk({ ...risk, require_stop: v })} label="Stop obligatoire" />}</Field>
              <Field label="Trading automatique" hint="Désactivé : les agents n’ouvrent plus de position sur ce portefeuille.">{() => <Switch checked={risk.auto_trade !== false} onChange={(v) => setRisk({ ...risk, auto_trade: v })} label="Trading automatique" />}</Field>
              <Field label="Contrôle Risk des ordres manuels">{() => <Switch checked={!!risk.gate_manual} onChange={(v) => setRisk({ ...risk, gate_manual: v })} label="Contrôle Risk des ordres manuels" />}</Field>
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}

export default function Portfolios() {
  const toast = useToast();
  const pf = usePortfolios(5000);
  const [edit, setEdit] = useState<Portfolio | null | 'new'>(null);
  const [reset, setReset] = useState<Portfolio | null>(null);
  const [del, setDel] = useState<Portfolio | null>(null);
  const p = pf.current;
  const eq = useApi<{ items: { ts: string; equity: number }[] }>(p ? `/api/portfolios/${p.id}/equity?limit=1500` : null, 30000);
  const stats = useApi<any>(p ? `/api/portfolios/${p.id}/stats` : null, 15000);
  const replay = useApi<any>(p ? `/api/portfolios/${p.id}/replay-check` : null, 0);
  useEffect(() => { replay.reload(); }, [p?.realized_pnl, p?.balance]); // eslint-disable-line react-hooks/exhaustive-deps
  const toggleActive = async (x: Portfolio, v: boolean) => { try { await api(`/api/portfolios/${x.id}`, { method: 'PATCH', body: { active: v } }); pf.reload(); } catch (e) { toast('error', 'Échec', (e as Error).message); } };
  return (
    <Page title="Portefeuilles" subtitle="Comptes papier indépendants : capital, levier, frais, glissement et limites de risque propres." actions={<button className="btn primary" onClick={() => setEdit('new')}>Nouveau portefeuille</button>}>
      <Async state={pf}>
        {() => (
          <>
            <Panel flush>
              <DataTable rows={pf.items} rowKey={(r) => r.id} selected={pf.sel} onRow={(r) => pf.setSel(r.id)} empty={<Empty title="Aucun portefeuille" hint="Créez un portefeuille papier pour démarrer." action={<button className="btn primary" onClick={() => setEdit('new')}>Créer un portefeuille</button>} />} cols={[
                { key: 'n', header: 'Nom', render: (r) => <span className="sym">{r.name}</span> },
                { key: 'e', header: 'Equity', num: true, render: (r) => usd(r.equity) },
                { key: 'r', header: 'Rendement', num: true, render: (r) => <span className={tone(r.return_pct)}>{pct(r.return_pct)}</span> },
                { key: 'l', header: 'Levier', num: true, hideSm: true, render: (r) => `${r.leverage}x` },
                { key: 'p', header: 'Positions', num: true, hideSm: true, render: (r) => r.positions.length },
                { key: 'd', header: '', render: (r) => <span onClick={(e) => e.stopPropagation()}><button className="btn sm danger" disabled={r.id === pf.sel || pf.items.length < 2} title={r.id === pf.sel ? 'Portefeuille en cours d’utilisation : sélectionnez-en un autre pour pouvoir le supprimer' : pf.items.length < 2 ? 'Impossible de supprimer le dernier portefeuille' : `Supprimer ${r.name}`} onClick={() => setDel(r)}>Supprimer</button></span> },
                { key: 'a', header: 'Actif', render: (r) => <span onClick={(e) => e.stopPropagation()}><Switch checked={r.active} onChange={(v) => toggleActive(r, v)} label={`Activer ${r.name}`} /></span> },
              ]} />
            </Panel>
            {p && (
              <>
                <div className="row between"><h2>{p.name}</h2><div className="row"><button className="btn" onClick={() => setEdit(p)}>Modifier</button><button className="btn danger" onClick={() => setReset(p)}>Réinitialiser</button></div></div>
                <div className="grid c4 keep2">
                  <Kpi label="Equity" value={usd(p.equity)} sub={`Solde ${usd(p.balance)}`} />
                  <Kpi label="P&L réalisé" value={usd(p.realized_pnl)} tone={tone(p.realized_pnl)} sub={`Frais ${usd(p.fees_paid)}`} />
                  <Kpi label="Marge utilisée" value={usd(p.margin_used)} sub={`Libre ${usd(p.free_margin)}`} />
                  <Kpi label="Exposition" value={`${num(p.exposure_pct, 0)} %`} sub={usd(p.exposure_usd, 0)} />
                </div>
                <div className="grid c2">
                  <Panel title="Courbe d’equity"><Async state={eq}>{(d) => (d.items.length > 1 ? <EquityChart points={d.items} /> : <Empty title="Pas encore d’historique" />)}</Async></Panel>
                  <Panel title="Performance (trades clôturés)"><Async state={stats}>{(s) => (s.closed_trades ? <KV rows={[['Trades clôturés', s.closed_trades], ['Taux de réussite', s.win_rate !== null ? `${(s.win_rate * 100).toFixed(1)} %` : '—'], ['Profit factor', s.profit_factor ?? '—'], ['Gain moyen', usd(s.avg_win)], ['Perte moyenne', usd(s.avg_loss)], ['Profit brut', usd(s.gross_profit)], ['Perte brute', usd(s.gross_loss)]]} /> : <Empty title="Aucun trade clôturé" hint="Les statistiques sont calculées uniquement à partir des exécutions réelles du moteur papier." />)}</Async></Panel>
                </div>
                <Panel title="Cohérence du journal d’exécutions">
                  <Async state={replay}>{(r) => <div className="row"><Pill kind={r.consistent ? 'ok' : 'err'}>{r.consistent ? 'Cohérent' : 'Écart détecté'}</Pill><span className="muted">{r.consistent ? 'L’état reconstitué à partir des exécutions est identique à l’état stocké.' : JSON.stringify(r.differences ?? r).slice(0, 220)}</span></div>}</Async>
                </Panel>
              </>
            )}
          </>
        )}
      </Async>
      {edit && <Editor p={edit === 'new' ? null : edit} onClose={() => setEdit(null)} onSaved={pf.reload} />}
      {del && <Confirm title={`Supprimer « ${del.name} » ?`} danger confirmLabel="Supprimer" message={`Le portefeuille, ses positions${del.positions.length ? ` (${del.positions.length} ouverte(s))` : ''}, ses ordres, ses exécutions et sa courbe d’equity seront supprimés définitivement. Cette action est irréversible.`} onConfirm={async () => { try { await api(`/api/portfolios/${del.id}`, { method: 'DELETE' }); toast('success', 'Portefeuille supprimé'); pf.reload(); } catch (e) { toast('error', 'Échec', (e as Error).message); } }} onClose={() => setDel(null)} />}
      {reset && <Confirm title={`Réinitialiser « ${reset.name} » ?`} danger confirmLabel="Réinitialiser" message="Positions, ordres, exécutions et courbe d’equity de ce portefeuille seront effacés ; le capital initial est rétabli." onConfirm={async () => { try { await api(`/api/portfolios/${reset.id}/reset`, { method: 'POST', body: {} }); pf.reload(); } catch (e) { toast('error', 'Échec', (e as Error).message); } }} onClose={() => setReset(null)} />}
    </Page>
  );
}
