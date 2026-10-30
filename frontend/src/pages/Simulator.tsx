import { useState } from 'react';
import { Banner, Empty, Field, JsonBlock, KV, Page, Panel, Pill } from '../components/ui';
import { PortfolioSelect } from '../components/PortfolioSelect';
import { api } from '../lib/api';
import { useMarket } from '../lib/market';
import { usePortfolios } from '../lib/portfolio';
import { usd } from '../lib/format';

const num = (v: string) => (v.trim() === '' ? null : Number(v.replace(',', '.')));
function Step({ title, d, children }: { title: string; d?: any; children?: React.ReactNode }) {
  if (!d) return null;
  return (
    <Panel title={title} actions={<Pill kind={d.status === 'ok' ? 'ok' : d.status === 'error' ? 'err' : 'warn'}>{d.status}</Pill>}>
      <div className="stack"><p>{d.summary}</p>{children}</div>
    </Panel>
  );
}
export default function Simulator() {
  const { symbol: current } = useMarket();
  const pf = usePortfolios(0);
  const [symbol, setSymbol] = useState(current);
  const [entry, setEntry] = useState('');
  const [stop, setStop] = useState('');
  const [target, setTarget] = useState('');
  const [stopRule, setStopRule] = useState('');
  const [targetRule, setTargetRule] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [res, setRes] = useState<any>(null);
  const run = async () => {
    setBusy(true); setErr(null);
    try { setRes(await api('/api/simulator/plan', { body: { symbol, portfolio_id: pf.sel, entry: num(entry), stop: num(stop), target: num(target), stop_rule: stopRule || null, target_rule: targetRule || null } })); }
    catch (e) { setErr((e as Error).message); setRes(null); } finally { setBusy(false); }
  };
  const rk = res?.risk;
  return (
    <Page title="Simulateur" subtitle="Dry run : plan de sortie → TimeMachine → dimensionnement → contrôle Risk, sans créer d’ordre ni de proposition.">
      <div className="grid side">
        <div className="stack">
          <Panel title="Paramètres" actions={<button className="btn primary" onClick={run} disabled={busy || pf.sel <= 0}>{busy ? 'Simulation…' : 'Simuler'}</button>}>
            <div className="form-grid">
              <Field label="Portefeuille">{() => <PortfolioSelect items={pf.items} value={pf.sel} onChange={pf.setSel} />}</Field>
              <Field label="Instrument">{(id) => <input id={id} className="input" value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} />}</Field>
              <Field label="Entrée (option)" hint="Vide = prix courant">{(id) => <input id={id} className="input" inputMode="decimal" value={entry} onChange={(e) => setEntry(e.target.value)} />}</Field>
              <Field label="Stop (prix)">{(id) => <input id={id} className="input" inputMode="decimal" value={stop} onChange={(e) => setStop(e.target.value)} />}</Field>
              <Field label="Cible (prix)">{(id) => <input id={id} className="input" inputMode="decimal" value={target} onChange={(e) => setTarget(e.target.value)} />}</Field>
              <Field label="Règle de stop" hint="Ex. pct:0.3, atr:1.5 ou price:1.0850">{(id) => <input id={id} className="input" value={stopRule} onChange={(e) => setStopRule(e.target.value)} />}</Field>
              <Field label="Règle de cible" hint="Ex. 2R, pct:0.6">{(id) => <input id={id} className="input" value={targetRule} onChange={(e) => setTargetRule(e.target.value)} />}</Field>
            </div>
          </Panel>
          {err && <Banner kind="err">{err}</Banner>}
          {!res && !err && <Panel><Empty title="Aucune simulation" hint="Choisissez un instrument et un portefeuille, puis lancez la simulation." /></Panel>}
          {res && (
            <>
              <Step title="Agent Exit — plan" d={res.exit}>{res.exit.plan && <KV rows={[['Entrée', res.exit.plan.entry], ['Stop', res.exit.plan.stop], ['Cible', res.exit.plan.target], ['Heure de sortie forcée', res.exit.plan.flat_by]]} />}</Step>
              <Step title="Agent TimeMachine" d={res.timemachine}><JsonBlock data={res.timemachine} /></Step>
              <Step title="Agent Budget — taille" d={res.budget}>{res.budget && <KV rows={[['Quantité', res.budget.qty], ['Notionnel', usd(res.budget.notional_usd)], ['Marge', usd(res.budget.margin_usd)], ['Perte max', usd(res.budget.max_loss_usd)], ['Plafond atteint', res.budget.bound_by], ['R:R', res.budget.reward_risk], ['Refus', res.budget.refusal]]} />}</Step>
              <Step title="Agent Risk — verdict" d={rk}>{rk && <><div className="row"><Pill kind={rk.verdict === 'PASS' ? 'ok' : 'err'}>{rk.verdict}</Pill>{!!rk.reasons?.length && <span>{rk.reasons.join(' · ')}</span>}</div>{rk.warnings?.map((w: string) => <Banner key={w} kind="warn">{w}</Banner>)}<JsonBlock data={rk.backend_rules} /></>}</Step>
              <Banner kind="info">Simulation à blanc : aucun ordre n’a été envoyé.</Banner>
            </>
          )}
        </div>
        <Panel title="Comment lire ce résultat"><p className="muted">Chaque étape est celle de l’agent correspondant, avec les mêmes règles que le pipeline automatique. Le verdict PASS signifie que l’ordre serait accepté <em>maintenant</em> ; un signal réel doit en plus être détecté par l’Agent Entry.</p></Panel>
      </div>
    </Page>
  );
}
