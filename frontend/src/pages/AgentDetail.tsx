import { ModelSelect } from './Settings';
import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { Async, Banner, DataTable, Empty, ErrorState, Field, JsonBlock, KV, Modal, Page, Panel, Pill, Switch, Tabs, useToast } from '../components/ui';
import { api } from '../lib/api';
import { useApi } from '../lib/hooks';
import { IMPL, RUN_EXAMPLE, RUN_KIND } from '../lib/agents';
import { dateTime, ms } from '../lib/format';

type Tab = 'apercu' | 'config' | 'run' | 'histo' | 'skill';

function ParamField({ k, def, value, help, onChange }: { k: string; def: unknown; value: unknown; help?: string; onChange: (v: unknown) => void }) {
  if (typeof def === 'boolean') return <Field label={k} hint={help}>{() => <Switch checked={!!value} onChange={onChange} label={k} />}</Field>;
  if (typeof def === 'number') return <Field label={k} hint={help}>{(id) => <input id={id} className="input" inputMode="decimal" value={String(value ?? '')} onChange={(e) => { const n = Number(e.target.value.replace(',', '.')); onChange(e.target.value === '' || Number.isNaN(n) ? e.target.value : n); }} />}</Field>;
  if (typeof def === 'string' || def === null) return <Field label={k} hint={help}>{(id) => <input id={id} className="input" value={String(value ?? '')} onChange={(e) => onChange(e.target.value)} />}</Field>;
  return <Field label={k} hint={help ?? 'JSON'}>{(id) => <textarea id={id} className="input mono" defaultValue={JSON.stringify(value)} onBlur={(e) => { try { onChange(JSON.parse(e.target.value)); } catch { /* ignore */ } }} />}</Field>;
}

export default function AgentDetail() {
  const { id = '' } = useParams();
  const toast = useToast();
  const a = useApi<any>(`/api/agents/${id}`, 8000);
  const models = useApi<{ items: { id: string }[]; default: string }>('/api/llm/models', 0);
  const [tab, setTab] = useState<Tab>('apercu');
  const d = a.data;
  const [params, setParams] = useState<Record<string, unknown>>({});
  const [model, setModel] = useState('');
  const [interval, setInterval_] = useState('');
  const [enabled, setEnabled] = useState(true);
  useEffect(() => {
    if (!d) return;
    setParams(d.config.effective_params ?? {});
    setModel(d.config.model ?? '');
    setInterval_(d.config.interval_s ? String(d.config.interval_s) : '');
    setEnabled(d.config.enabled);
  }, [d?.id, d?.config?.enabled, JSON.stringify(d?.config?.params), d?.config?.model, d?.config?.interval_s]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = async () => {
    const changed: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(params)) if (JSON.stringify(v) !== JSON.stringify(d.default_params[k])) changed[k] = v;
    try {
      await api(`/api/agents/${id}/config`, { method: 'PUT', body: { enabled, model: model || null, clear_model: !model && !!d.config.model, params: changed, interval_s: interval ? Number(interval) : null } });
      toast('success', 'Configuration enregistrée');
      a.reload();
    } catch (e) { toast('error', 'Configuration refusée', (e as Error).message); }
  };
  const reset = async () => { try { await api(`/api/agents/${id}/reset`, { method: 'POST', body: {} }); toast('success', 'Valeurs par défaut rétablies'); a.reload(); } catch (e) { toast('error', 'Échec', (e as Error).message); } };

  return (
    <Page title={d?.name ?? id} subtitle={d?.mission} actions={<Link className="btn" to="/agents">Tous les agents</Link>}>
      {a.error && !d && <ErrorState message={a.error} onRetry={a.reload} />}
      {d && (
        <>
          <div className="row">
            <Pill kind={(IMPL[d.implementation] ?? IMPL.partial).kind}>{(IMPL[d.implementation] ?? IMPL.partial).label}</Pill>
            <Pill kind={d.config.enabled ? 'ok' : 'mute'}>{d.config.enabled ? 'Activé' : 'Désactivé'}</Pill>
            <span className="muted">Skill <code>{d.skill}</code> · version {String(d.skill_version).slice(0, 7)}</span>
          </div>
          <Tabs value={tab} onChange={setTab} tabs={[{ id: 'apercu', label: 'Aperçu' }, { id: 'config', label: 'Configuration' }, { id: 'run', label: 'Exécuter' }, { id: 'histo', label: 'Historique' }, { id: 'skill', label: 'Skill source' }]} />
          {tab === 'apercu' && (
            <div className="grid c2">
              <Panel title="Entrées et sorties"><KV rows={[['Entrées', <ul key="i" style={{ margin: 0, paddingLeft: 18 }}>{d.inputs.map((x: string) => <li key={x}>{x}</li>)}</ul>], ['Sorties', <ul key="o" style={{ margin: 0, paddingLeft: 18 }}>{d.outputs.map((x: string) => <li key={x}>{x}</li>)}</ul>], ['Dépendances', <ul key="d" style={{ margin: 0, paddingLeft: 18 }}>{d.dependencies.map((x: string) => <li key={x}>{x}</li>)}</ul>], ['Utilise l’IA', d.uses_llm ? 'Oui, pour résumer des faits déjà calculés (optionnel)' : 'Non'], ['Délai maximal', `${d.config.timeout_s} s, ${d.config.retries} nouvelle(s) tentative(s)`]]} /></Panel>
              <Panel title="Fidélité au skill d’origine et limites"><ul style={{ margin: 0, paddingLeft: 18, display: 'grid', gap: 6 }}>{d.limitations.map((x: string) => <li key={x}>{x}</li>)}</ul></Panel>
            </div>
          )}
          {tab === 'config' && (
            <Panel title="Configuration" actions={<><button className="btn" onClick={reset}>Valeurs par défaut</button><button className="btn primary" onClick={save}>Enregistrer</button></>}>
              <div className="stack">
                <div className="form-grid">
                  <Field label="Activé" hint="Un agent désactivé n’est plus déclenché automatiquement ; une exécution manuelle reste possible.">{() => <Switch checked={enabled} onChange={setEnabled} label="Activé" />}</Field>
                  <Field label="Intervalle automatique (s)" hint={d.default_interval_s ? `Par défaut ${d.default_interval_s} s` : 'Déclenché par événement'}>{(i) => <input id={i} className="input" inputMode="numeric" value={interval} onChange={(e) => setInterval_(e.target.value)} />}</Field>
                  {d.uses_llm && <Field label="Modèle OpenRouter" hint="Par défaut, l’agent suit le modèle choisi dans Réglages › IA : il change tout seul avec lui.">{(i) => <ModelSelect id={i} value={model} items={models.data?.items ?? []} inherit={models.data?.default ?? 'minimax/minimax-m3.1-flash'} onChange={setModel} />}</Field>}
                </div>
                {d.uses_llm && models.error && <Banner kind="warn">Catalogue de modèles indisponible ({models.error}). Vous pouvez saisir l’identifiant du modèle à la main.</Banner>}
                {Object.keys(d.default_params).length ? (
                  <div className="form-grid">{Object.entries(d.default_params).map(([k, def]) => <ParamField key={k} k={k} def={def} value={params[k]} help={d.param_help?.[k]} onChange={(v) => setParams((p) => ({ ...p, [k]: v }))} />)}</div>
                ) : <span className="muted">Cet agent n’a pas de paramètre.</span>}
              </div>
            </Panel>
          )}
          {tab === 'run' && <RunPanel id={id} runnable={id !== 'broker'} onDone={a.reload} />}
          {tab === 'histo' && <History id={id} />}
          {tab === 'skill' && <SkillPanel id={id} />}
        </>
      )}
    </Page>
  );
}

function RunPanel({ id, runnable, onDone }: { id: string; runnable: boolean; onDone: () => void }) {
  const [text, setText] = useState(JSON.stringify(RUN_EXAMPLE[id] ?? {}, null, 2));
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    let inputs: unknown;
    try { inputs = JSON.parse(text || '{}'); } catch { setErr('JSON invalide'); return; }
    setBusy(true); setErr(null);
    try { setRes(await api(`/api/agents/${id}/run`, { body: { inputs } })); onDone(); } catch (e) { setErr((e as Error).message); setRes(null); } finally { setBusy(false); }
  };
  if (!runnable) return <Panel title="Exécuter"><Empty title="Exécution manuelle indisponible" hint="Le Broker ne s’exécute que sur une proposition validée par l’Agent Risk. Utilisez le pipeline, ou la page Ordres pour approuver une proposition." /></Panel>;
  return (
    <div className="grid c2">
      <Panel title="Entrées (JSON)" actions={<button className="btn primary" onClick={run} disabled={busy}>{busy ? 'Exécution…' : 'Exécuter'}</button>}>
        <textarea className="input mono" style={{ minHeight: 180 }} aria-label="Entrées JSON" value={text} onChange={(e) => setText(e.target.value)} />
        <p className="muted" style={{ marginTop: 8 }}>L’exécution manuelle ne prend jamais de position : seule la chaîne Entry → Risk → Broker le fait.</p>
      </Panel>
      <Panel title="Résultat">
        {err && <Banner kind="err">{err}</Banner>}
        {res ? <div className="stack"><div className="row"><Pill kind={RUN_KIND[res.status] ?? 'mute'}>{res.status}</Pill><span>{res.summary}</span></div>{res.warnings?.map((w: string) => <Banner key={w} kind="warn">{w}</Banner>)}<JsonBlock data={res.output} /></div> : !err && <Empty title="Aucun résultat" hint="Lancez l’agent pour voir sa sortie structurée." />}
      </Panel>
    </div>
  );
}

function History({ id }: { id: string }) {
  const r = useApi<{ items: any[] }>(`/api/runs?agent=${id}&limit=100`, 8000);
  const [open, setOpen] = useState<number | null>(null);
  const detail = useApi<any>(open ? `/api/runs/${open}` : null);
  return (
    <Panel flush title="Historique des exécutions">
      <Async state={r}>{(d) => <DataTable rows={d.items} rowKey={(x) => x.id} onRow={(x) => setOpen(x.id)} empty={<Empty title="Aucune exécution" />} cols={[
        { key: 't', header: 'Début', render: (x) => dateTime(x.started_at) },
        { key: 's', header: 'Statut', render: (x) => <Pill kind={RUN_KIND[x.status] ?? 'mute'}>{x.status}</Pill> },
        { key: 'tr', header: 'Déclencheur', hideSm: true, render: (x) => x.trigger },
        { key: 'i', header: 'Instrument', hideSm: true, render: (x) => x.instrument ?? '—' },
        { key: 'd', header: 'Durée', num: true, render: (x) => ms(x.duration_ms) },
        { key: 'r', header: 'Résumé', render: (x) => <span className="muted">{x.error || x.summary}</span> },
      ]} />}</Async>
      {open && (
        <Modal title={`Exécution #${open}`} onClose={() => setOpen(null)}>
          {detail.data ? <div className="stack"><KV rows={[['Agent', detail.data.agent], ['Skill', String(detail.data.skill_version).slice(0, 7)], ['Modèle', detail.data.model], ['Corrélation', detail.data.correlation_id], ['Qualité des données', detail.data.data_quality], ['Erreur', detail.data.error]]} /><h3>Entrées</h3><JsonBlock data={detail.data.input} /><h3>Sorties</h3><JsonBlock data={detail.data.output} /></div> : detail.error ? <ErrorState message={detail.error} /> : <Empty title="Chargement…" />}
        </Modal>
      )}
    </Panel>
  );
}

function SkillPanel({ id }: { id: string }) {
  const s = useApi<{ skill_md: string; integrity: Record<string, boolean>; provenance: any }>(`/api/agents/${id}/skill`);
  const prov = s.data?.provenance;
  const md = useMemo(() => s.data?.skill_md ?? '', [s.data]);
  return (
    <Async state={s}>{() => (
      <div className="stack">
        <Banner kind="info">Texte du skill vendorisé sans modification — {prov.repo} @ {String(prov.commit).slice(0, 7)} ({prov.license}). Les adaptations se trouvent dans le code de l’agent, jamais dans le skill.</Banner>
        <Panel title="SKILL.md"><pre className="json" style={{ maxHeight: 520, whiteSpace: 'pre-wrap' }}>{md || 'Fichier indisponible.'}</pre></Panel>
      </div>
    )}</Async>
  );
}
