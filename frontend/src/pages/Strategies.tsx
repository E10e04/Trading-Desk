import { useState } from 'react';
import { Async, Banner, Confirm, DataTable, Empty, Field, Modal, Page, Panel, Pill, Switch, useToast } from '../components/ui';
import { api } from '../lib/api';
import { useApi } from '../lib/hooks';
import { usePortfolios } from '../lib/portfolio';

const HELP: Record<string, string> = { setup: 'orb (cassure de la plage d’ouverture)', or_minutes: 'durée de la plage d’ouverture (min)', stop: 'règle de stop (ex. pct:0.3, atr:1.5)', target: 'règle de cible (ex. 2R)', flat_by: 'heure de sortie forcée (HH:MM, heure de la séance)', no_entries_before: 'pas d’entrée avant (HH:MM)', no_entries_after: 'pas d’entrée après (HH:MM)' };

function Editor({ s, allowed, onClose, onSaved }: { s: any | null; allowed: string[]; onClose: () => void; onSaved: () => void }) {
  const pf = usePortfolios(0);
  const [name, setName] = useState(s?.name ?? '');
  const [desc, setDesc] = useState(s?.description ?? '');
  const [portfolio, setPortfolio] = useState<number | ''>(s?.portfolio_id ?? '');
  const [params, setParams] = useState<Record<string, string>>(Object.fromEntries(Object.entries(s?.params ?? {}).map(([k, v]) => [k, String(v)])));
  const [err, setErr] = useState<string | null>(null);
  const save = async () => {
    const clean: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(params)) if (v.trim() !== '') clean[k] = /^-?\d+(\.\d+)?$/.test(v.trim()) ? Number(v) : v.trim();
    const body = { name, description: desc, portfolio_id: portfolio === '' ? null : portfolio, params: clean, ...(s ? {} : { enabled: false }) };
    try { await api(s ? `/api/strategies/${s.id}` : '/api/strategies', { method: s ? 'PATCH' : 'POST', body }); onSaved(); onClose(); } catch (e) { setErr((e as Error).message); }
  };
  return (
    <Modal title={s ? 'Modifier la stratégie' : 'Nouvelle stratégie'} onClose={onClose} footer={<><button className="btn" onClick={onClose}>Annuler</button><button className="btn primary" disabled={!name.trim()} onClick={save}>Enregistrer</button></>}>
      <div className="stack">
        {err && <Banner kind="err">{err}</Banner>}
        <Field label="Nom">{(id) => <input id={id} className="input" value={name} onChange={(e) => setName(e.target.value)} />}</Field>
        <Field label="Description">{(id) => <textarea id={id} className="input" value={desc} onChange={(e) => setDesc(e.target.value)} />}</Field>
        <Field label="Portefeuille" hint="Vide = tous les portefeuilles actifs">{(id) => <select id={id} className="input" value={portfolio} onChange={(e) => setPortfolio(e.target.value === '' ? '' : Number(e.target.value))}><option value="">Tous</option>{pf.items.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select>}</Field>
        <h3>Paramètres du skill Entry</h3>
        <div className="form-grid">{allowed.map((k) => <Field key={k} label={k} hint={HELP[k]}>{(id) => <input id={id} className="input" value={params[k] ?? ''} onChange={(e) => setParams({ ...params, [k]: e.target.value })} />}</Field>)}</div>
      </div>
    </Modal>
  );
}

export default function Strategies() {
  const toast = useToast();
  const st = useApi<{ items: any[]; allowed_params: string[] }>('/api/strategies');
  const pf = usePortfolios(0);
  const [edit, setEdit] = useState<any | null | 'new'>(null);
  const [del, setDel] = useState<any>(null);
  const names = Object.fromEntries(pf.items.map((p) => [p.id, p.name]));
  const toggle = async (s: any, v: boolean) => { try { await api(`/api/strategies/${s.id}`, { method: 'PATCH', body: { enabled: v } }); st.reload(); } catch (e) { toast('error', 'Échec', (e as Error).message); } };
  return (
    <Page title="Stratégies" subtitle="Réglages d’entrée et de sortie appliqués par les agents Entry, Exit et Budget. Seuls les paramètres réellement pris en charge par le skill sont proposés." actions={<button className="btn primary" onClick={() => setEdit('new')}>Nouvelle stratégie</button>}>
      <Banner kind="info">Sans stratégie active, les agents utilisent leurs paramètres par défaut (configurables dans « Agents IA »).</Banner>
      <Panel flush>
        <Async state={st}>{(d) => <DataTable rows={d.items} rowKey={(r) => r.id} onRow={setEdit} empty={<Empty title="Aucune stratégie" hint="Créez une stratégie pour ajuster la plage d’ouverture, le stop ou la cible." />} cols={[
          { key: 'n', header: 'Nom', render: (r) => <span className="sym">{r.name}</span> },
          { key: 'd', header: 'Description', hideSm: true, render: (r) => <span className="muted">{r.description}</span> },
          { key: 'p', header: 'Portefeuille', hideSm: true, render: (r) => (r.portfolio_id ? names[r.portfolio_id] ?? r.portfolio_id : 'Tous') },
          { key: 'pa', header: 'Paramètres', render: (r) => (Object.keys(r.params).length ? Object.entries(r.params).map(([k, v]) => `${k}=${v}`).join(' · ') : <Pill kind="mute" plain>par défaut</Pill>) },
          { key: 'e', header: 'Active', render: (r) => <span onClick={(e) => e.stopPropagation()}><Switch checked={r.enabled} onChange={(v) => toggle(r, v)} label={`Activer ${r.name}`} /></span> },
          { key: 'x', header: '', render: (r) => <button className="btn sm ghost" aria-label={`Supprimer ${r.name}`} onClick={(e) => { e.stopPropagation(); setDel(r); }}>Supprimer</button> },
        ]} />}</Async>
      </Panel>
      {edit && <Editor s={edit === 'new' ? null : edit} allowed={st.data?.allowed_params ?? []} onClose={() => setEdit(null)} onSaved={st.reload} />}
      {del && <Confirm title="Supprimer la stratégie ?" danger confirmLabel="Supprimer" message={`« ${del.name} » sera supprimée définitivement.`} onConfirm={async () => { await api(`/api/strategies/${del.id}`, { method: 'DELETE' }); st.reload(); }} onClose={() => setDel(null)} />}
    </Page>
  );
}
