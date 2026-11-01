import { useEffect, useState } from 'react';
import { Async, Banner, Confirm, DataTable, Empty, Field, KV, Page, Panel, Pill, Segmented, Switch, Tabs, useToast } from '../components/ui';
import { api } from '../lib/api';
import { useApi } from '../lib/hooks';
import { useTheme } from '../lib/theme';
import { useUpdate } from '../lib/update';
import { InstrumentPicker } from '../components/InstrumentPicker';
import { AGENT_LABEL } from '../lib/agents';
import { useMarket } from '../lib/market';
import { dateTime, ms, usd } from '../lib/format';
import { desktopNotifEnabled, requestWebNotifPermission, sendSystemNotification, setDesktopNotifEnabled, webNotifPermission } from '../lib/desktopNotify';

type Tab = 'exec' | 'data' | 'ai' | 'watch' | 'misc';
type Vals = Record<string, any>;

function useSettings() {
  const st = useApi<{ values: Vals; defaults: Vals }>('/api/settings');
  const toast = useToast();
  const save = async (patch: Vals, ok = 'Réglage enregistré') => {
    try { await api('/api/settings', { method: 'PUT', body: patch }); toast('success', ok); st.reload(); } catch (e) { toast('error', 'Réglage refusé', (e as Error).message); }
  };
  return { st, save };
}
function Row({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return <div className="row between" style={{ padding: '10px 0', borderBottom: '1px solid var(--border)', alignItems: 'flex-start' }}><div style={{ maxWidth: '60ch' }}><b>{title}</b>{hint && <div className="muted">{hint}</div>}</div>{children}</div>;
}
function NumberSetting({ k, vals, save, nullable }: { k: string; vals: Vals; save: (p: Vals, ok?: string) => void; nullable?: boolean }) {
  const [v, setV] = useState(vals[k] === null || vals[k] === undefined ? '' : String(vals[k]));
  useEffect(() => setV(vals[k] === null || vals[k] === undefined ? '' : String(vals[k])), [vals[k]]); // eslint-disable-line react-hooks/exhaustive-deps
  const commit = () => {
    const t = v.trim().replace(',', '.');
    if (t === '' && nullable) return vals[k] !== null && save({ [k]: null });
    const n = Number(t);
    if (!Number.isNaN(n) && n !== vals[k]) save({ [k]: n });
  };
  return <input className="input" style={{ width: 120 }} aria-label={k} inputMode="decimal" value={v} onChange={(e) => setV(e.target.value)} onBlur={commit} onKeyDown={(e) => e.key === 'Enter' && commit()} />;
}

export default function Settings() {
  const [tab, setTab] = useState<Tab>('exec');
  const { st, save } = useSettings();
  return (
    <Page title="Réglages" subtitle="Exécution, données, intelligence artificielle, surveillance et apparence.">
      <Tabs value={tab} onChange={setTab} tabs={[{ id: 'exec', label: 'Exécution' }, { id: 'data', label: 'Données' }, { id: 'ai', label: 'Intelligence artificielle' }, { id: 'watch', label: 'Surveillance et alertes' }, { id: 'misc', label: 'Apparence et sources' }]} />
      <Async state={st}>
        {(d) => (
          <>
            {tab === 'exec' && <><Exec vals={d.values} save={save} /><ShortSelling vals={d.values} save={save} /></>}
            {tab === 'data' && <Data vals={d.values} save={save} />}
            {tab === 'ai' && <AI vals={d.values} save={save} />}
            {tab === 'watch' && <Watch vals={d.values} save={save} />}
            {tab === 'misc' && <Misc />}
          </>
        )}
      </Async>
    </Page>
  );
}

function Exec({ vals, save }: { vals: Vals; save: (p: Vals, ok?: string) => void }) {
  const [kill, setKill] = useState(false);
  return (
    <Panel title="Exécution (paper trading uniquement)">
      <Banner kind="info">Cette application ne peut pas envoyer d’ordre à un courtier réel. Toutes les positions sont simulées par le moteur papier.</Banner>
      <Row title="Exécution automatique" hint="Quand un signal d’entrée est détecté et validé par l’Agent Risk, la position papier est prise immédiatement, sans attendre votre accord. Désactivée : chaque proposition attend votre approbation (page Ordres)."><Switch checked={!!vals.auto_execute} onChange={(v) => save({ auto_execute: v })} label="Exécution automatique" /></Row>
      <Row title="Pipeline automatique" hint="L’orchestrateur déclenche Scanner, News, Entry, Exit, Risk… tout seul. En pause : seuls les lancements manuels fonctionnent."><Switch checked={!!vals.pipeline_enabled} onChange={(v) => save({ pipeline_enabled: v })} label="Pipeline automatique" /></Row>
      <Row title="Interrupteur d’arrêt global" hint="Bloque immédiatement toute nouvelle position automatique."><Switch checked={!!vals.kill_switch} onChange={(v) => (v ? setKill(true) : save({ kill_switch: false }))} label="Interrupteur d’arrêt" /></Row>
      <Row title="Validité d’une proposition en attente (min)" hint="Au-delà, elle expire et le contrôle Risk est à refaire."><NumberSetting k="proposal_ttl_min" vals={vals} save={save} /></Row>
      <Row title="Écart TimeMachine minimal" hint="Vide = informatif. Sinon Risk bloque si la cible n’est pas favorisée d’au moins cet écart (0 : la cible doit être favorisée)."><NumberSetting k="timemachine_min_edge" vals={vals} save={save} nullable /></Row>
      <Row title="Bloquer sur halte de cotation (News)" hint="Une halte signalée par l’Agent News empêche l’entrée."><Switch checked={!!vals.news_block_on_halt} onChange={(v) => save({ news_block_on_halt: v })} label="Bloquer sur halte" /></Row>
      {kill && <Confirm title="Activer l’interrupteur d’arrêt ?" danger confirmLabel="Activer" message="Aucune nouvelle position automatique ne sera ouverte." onConfirm={() => save({ kill_switch: true })} onClose={() => setKill(false)} />}
    </Panel>
  );
}

function ShortSelling({ vals, save }: { vals: Vals; save: (p: Vals, ok?: string) => void }) {
  const toast = useToast();
  const pf = useApi<{ items: { id: number; name: string; active: boolean; risk?: { allow_short?: boolean } }[] }>('/api/portfolios', 0);
  const [ask, setAsk] = useState(false);
  const on = !!vals.allow_short;
  const dir = (vals.trade_direction as string) || 'long';
  const setPortfolio = async (id: number, v: boolean) => {
    try { await api(`/api/portfolios/${id}`, { method: 'PATCH', body: { risk: { allow_short: v } } }); pf.reload(); toast('success', v ? 'Ventes à découvert activées pour ce portefeuille' : 'Ventes à découvert désactivées pour ce portefeuille'); }
    catch (e) { toast('error', 'Modification impossible', (e as Error).message); }
  };
  return (
    <Panel title="Ventes à découvert (paper trading)">
      <Banner kind="info">Par défaut, les agents n’achètent que sur cassure à la hausse. Pour vendre sur cassure à la baisse, activez l’interrupteur global, celui du portefeuille, puis choisissez le sens. Tant qu’un des trois manque, rien ne change.</Banner>
      <Row title="1. Autoriser les ventes à découvert (global)" hint="Interrupteur général. Désactivé : aucun ordre de vente à découvert n’est évalué ni pris. Les positions à découvert déjà ouvertes restent gérées jusqu’à leur stop, leur cible ou leur clôture.">
        <Switch checked={on} onChange={(v) => (v ? setAsk(true) : save({ allow_short: false }, 'Ventes à découvert désactivées'))} label="Ventes à découvert (global)" />
      </Row>
      <div style={{ padding: '10px 0', borderBottom: '1px solid var(--border)' }}>
        <b>2. Portefeuilles autorisés</b>
        <div className="muted">Le portefeuille doit aussi avoir l’interrupteur activé.</div>
        <Async state={pf}>{(d) => d.items.length ? <div className="stack" style={{ marginTop: 8 }}>{d.items.map((p) => (
          <div key={p.id} className="row between"><span>{p.name}{!p.active && <span className="muted"> (inactif)</span>}</span>
            <Switch checked={!!p.risk?.allow_short} onChange={(v) => setPortfolio(p.id, v)} label={`Ventes à découvert : ${p.name}`} /></div>))}</div>
          : <div className="muted" style={{ marginTop: 8 }}>Aucun portefeuille : créez-en un dans Portefeuilles.</div>}</Async>
      </div>
      <Row title="3. Sens des signaux" hint="Achats seulement (comportement actuel), ventes seulement, ou les deux. Les ventes ne sont évaluées que si les interrupteurs 1 et 2 sont activés.">
        <Segmented label="Sens des signaux" value={dir} onChange={(v) => save({ trade_direction: v }, 'Sens des signaux enregistré')} options={[{ value: 'long', label: 'Achats' }, { value: 'short', label: 'Ventes' }, { value: 'both', label: 'Les deux' }]} />
      </Row>
      {on && dir !== 'long' && !(pf.data?.items ?? []).some((p) => p.active && p.risk?.allow_short) && <Banner kind="warn">Aucun portefeuille actif n’autorise encore les ventes : aucun ordre de vente ne sera pris.</Banner>}
      {ask && <Confirm title="Autoriser les ventes à découvert ?" confirmLabel="Autoriser" message="Les agents pourront ouvrir des positions à découvert (paper trading) sur les portefeuilles qui l’ont activé. Le risque d’une position à découvert n’est pas plafonné par le prix : le stop et la perte maximale par trade restent appliqués." onConfirm={() => save({ allow_short: true }, 'Ventes à découvert autorisées')} onClose={() => setAsk(false)} />}
    </Panel>
  );
}

function FeedDiagnostic() {
  const [res, setRes] = useState<{ steps: { step: string; ok: boolean; detail: string }[]; status?: { mode?: string; state?: string; last_error?: string } } | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async () => { setBusy(true); try { setRes(await api('/api/feed/diagnose', { method: 'POST' })); } catch (e) { setRes({ steps: [{ step: 'Diagnostic', ok: false, detail: (e as Error).message }] }); } finally { setBusy(false); } };
  return (
    <Panel title="Diagnostic du flux BiQuote">
      <div className="stack">
        <p className="muted">Teste chaque étape (REST, négociation SignalR, WebSocket, abonnement, réception) et indique laquelle échoue. Si le flux est coupé, l’application lit les prix en REST en attendant.</p>
        <div className="row"><button className="btn" disabled={busy} onClick={run}>{busy ? 'Test en cours…' : 'Tester la connexion'}</button>{res?.status?.mode && <Pill kind={res.status.mode === 'stream' ? 'ok' : 'warn'}>{res.status.mode === 'stream' ? 'Flux temps réel' : 'Secours REST'}</Pill>}</div>
        {res && <table className="tbl" aria-label="Résultat du diagnostic"><tbody>{res.steps.map((s) => <tr key={s.step}><td>{s.ok ? '✓' : '✗'} {s.step}</td><td>{s.detail}</td></tr>)}</tbody></table>}
      </div>
    </Panel>
  );
}

function Data({ vals, save }: { vals: Vals; save: (p: Vals, ok?: string) => void }) {
  return (
    <>
    <FeedDiagnostic />
    <Panel title="Données de marché">
      <Row title="Âge maximal d’une cotation pour exécuter (s)" hint="Un ordre n’est jamais exécuté sur une cotation plus vieille."><NumberSetting k="max_quote_age_s" vals={vals} save={save} /></Row>
      <Row title="Seuil « en direct » (s)" hint="Au-delà, la cotation est marquée périmée."><NumberSetting k="quote_live_s" vals={vals} save={save} /></Row>
      <Row title="Seuil de péremption forte (s)" hint="Au-delà, la cotation est considérée comme inutilisable."><NumberSetting k="quote_hard_stale_s" vals={vals} save={save} /></Row>
      <Row title="Source des cryptos" hint="Kraken : prix du marché réel, proche de TradingView. BiQuote : CFD d’un courtier MetaTrader 5 (peut différer). Effectif au prochain démarrage de l’application."><Segmented label="Source des cryptos" value={vals.crypto_source ?? 'kraken'} onChange={(v) => save({ crypto_source: v }, 'Source enregistrée : redémarrez l’application pour l’appliquer')} options={[{ value: 'kraken', label: 'Kraken' }, { value: 'biquote', label: 'BiQuote' }]} /></Row>
      <Row title="Univers du Scanner" hint="live : instruments avec cotation fraîche ; watchlist : liste surveillée ; all : tout le catalogue."><Segmented label="Univers" value={vals.scanner_universe} onChange={(v) => save({ scanner_universe: v })} options={[{ value: 'live', label: 'En direct' }, { value: 'watchlist', label: 'Surveillance' }, { value: 'all', label: 'Tout' }]} /></Row>
      <Row title="Instruments analysés par passage du Scanner"><NumberSetting k="scanner_max_instruments" vals={vals} save={save} /></Row>
      <Row title="Intervalle d’enregistrement de l’equity (s)"><NumberSetting k="equity_snapshot_s" vals={vals} save={save} /></Row>
    </Panel>
    </>
  );
}

const BUILTIN_MODEL = 'minimax/minimax-m3.1-flash';

export function ModelSelect({ id, value, items, onChange, inherit }: { id: string; value: string; items: { id: string; name?: string; free?: boolean }[]; onChange: (m: string) => void; inherit?: string }) {
  const [custom, setCustom] = useState(false);
  const known = (inherit !== undefined && value === '') || items.some((m) => m.id === value) || [BUILTIN_MODEL, `${BUILTIN_MODEL}:free`].includes(value);
  const free = items.filter((m) => m.free);
  const paid = items.filter((m) => !m.free);
  const fallback = !items.length;
  return (
    <div className="stack" style={{ gap: 6 }}>
      <select id={id} className="input" value={custom || !known ? '__custom' : value} onChange={(e) => { if (e.target.value === '__custom') setCustom(true); else { setCustom(false); onChange(e.target.value); } }}>
        {inherit !== undefined && <option value="">Modèle global ({inherit})</option>}
        {fallback ? <><option value={BUILTIN_MODEL}>MiniMax M3.1 Flash ({BUILTIN_MODEL})</option><option value={`${BUILTIN_MODEL}:free`}>MiniMax M3.1 Flash gratuit ({BUILTIN_MODEL}:free)</option></> : <>
          {!!free.length && <optgroup label="Gratuits">{free.map((m) => <option key={m.id} value={m.id}>{m.name && m.name !== m.id ? `${m.name} — ${m.id}` : m.id}</option>)}</optgroup>}
          <optgroup label="Payants">{paid.map((m) => <option key={m.id} value={m.id}>{m.name && m.name !== m.id ? `${m.name} — ${m.id}` : m.id}</option>)}</optgroup></>}
        <option value="__custom">Autre… (saisir l’identifiant)</option>
      </select>
      {(custom || !known) && <input className="input" aria-label="Identifiant du modèle" placeholder="fournisseur/modele" defaultValue={known ? '' : value} onBlur={(e) => e.target.value.trim() && onChange(e.target.value.trim())} onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).value.trim() && onChange((e.target as HTMLInputElement).value.trim())} />}
    </div>
  );
}

function AI({ vals, save }: { vals: Vals; save: (p: Vals, ok?: string) => void }) {
  const toast = useToast();
  const status = useApi<any>('/api/llm/status', 15000);
  const stats = useApi<any>('/api/llm/stats', 20000);
  const models = useApi<{ default: string; items: any[] }>('/api/llm/models');
  const agents = useApi<{ items: any[] }>('/api/agents');
  const [key, setKey] = useState('');
  const [test, setTest] = useState<any>(null);
  const [testing, setTesting] = useState(false);
  const [model, setModel] = useState<string | null>(null);
  const [del, setDel] = useState(false);
  const current = model ?? (vals.llm_model as string) ?? BUILTIN_MODEL;
  const freeSel = current.endsWith(':free') || !!models.data?.items.find((m) => m.id === current)?.free;
  const setKeyNow = async () => { try { await api('/api/llm/key', { method: 'PUT', body: { key } }); setKey(''); toast('success', 'Clé enregistrée sur le serveur'); status.reload(); models.reload(); } catch (e) { toast('error', 'Clé refusée', (e as Error).message); } };
  const runTest = async () => { setTesting(true); try { setTest(await api('/api/llm/test', { body: { model: current } })); stats.reload(); } catch (e) { setTest({ ok: false, error: (e as Error).message }); } finally { setTesting(false); } };
  const assign = async (id: string, m: string) => { try { await api(`/api/agents/${id}/config`, { method: 'PUT', body: { model: m || null, clear_model: !m } }); agents.reload(); toast('success', `Modèle de ${AGENT_LABEL[id] ?? id} enregistré`); } catch (e) { toast('error', 'Échec', (e as Error).message); } };
  const s = status.data;
  const u = stats.data?.today;
  return (
    <div className="stack">
      <Panel title="Clé OpenRouter">
        <div className="stack">
          <Banner kind="info">La clé est enregistrée côté serveur uniquement. Elle n’est jamais renvoyée au navigateur, ni écrite dans les journaux. Les calculs (prix, taille, risque) n’utilisent jamais l’IA.</Banner>
          <div className="row"><Pill kind={s?.configured ? 'ok' : 'warn'}>{s?.configured ? `Clé configurée ${s.masked}` : 'Aucune clé'}</Pill>{s?.configured && <span className="muted">source : {s.source}</span>}{s && !s.available && s.configured && <span className="warn">{s.unavailable_reason}</span>}</div>
          <div className="row"><input className="input" style={{ maxWidth: 420 }} type="password" autoComplete="off" placeholder="sk-or-…" aria-label="Clé OpenRouter" value={key} onChange={(e) => setKey(e.target.value)} /><button className="btn primary" disabled={!key.trim()} onClick={setKeyNow}>Enregistrer la clé</button>{s?.configured && <button className="btn danger" onClick={() => setDel(true)}>Supprimer</button>}</div>
        </div>
      </Panel>
      <div className="grid c2">
        <Panel title="Modèle d’IA" actions={<button className="btn" onClick={runTest} disabled={testing}>{testing ? 'Test…' : 'Tester la connexion'}</button>}>
          <div className="stack">
            <Field label="Modèle utilisé par défaut" hint={`Par défaut : ${BUILTIN_MODEL} (MiniMax M3.1 Flash). Chaque agent peut avoir son propre modèle (tableau plus bas).`}>{(id) => (
              <ModelSelect id={id} value={current} items={models.data?.items ?? []} onChange={(m) => { setModel(m); void save({ llm_model: m }, 'Modèle enregistré'); }} />
            )}</Field>
            <div className="row"><button className="btn sm" onClick={() => { setModel(BUILTIN_MODEL); void save({ llm_model: BUILTIN_MODEL }, 'Modèle par défaut rétabli'); }} disabled={current === BUILTIN_MODEL}>Rétablir MiniMax M3.1 Flash</button><button className="btn sm" onClick={() => models.reload()}>Actualiser la liste</button></div>
            {models.error && <Banner kind="warn">Liste des modèles indisponible ({models.error}). Vous pouvez saisir l’identifiant à la main dans la liste ci-dessus (option « Autre… »).</Banner>}
            {freeSel && <Banner kind="info">Modèle gratuit : quotas journaliers et par minute limités ; OpenRouter peut exiger de cocher l’utilisation des données dans <a href="https://openrouter.ai/settings/privacy" target="_blank" rel="noreferrer">vos paramètres de confidentialité</a>.</Banner>}
            {test && <Banner kind={test.ok ? 'info' : 'err'}>{test.ok ? <span>Connexion réussie avec <b>{test.model}</b> en {ms(test.latency_ms)}.{test.free_tier ? ' Compte gratuit : certaines limites s’appliquent.' : ''}</span> : <span><b>Échec du test.</b> {test.error}</span>}</Banner>}
            <span className="muted">{models.data ? `${models.data.items.length} modèles disponibles` : ''}</span>
            <p className="muted">Recherche web : aucun agent n’en a besoin aujourd’hui (News et Analyst résument des données déjà récupérées). Si vous en ajoutez, OpenRouter facture la recherche (≈ 0,007 $ la requête, Exa) <b>même avec un modèle gratuit</b> : il faut donc des crédits.</p>
          </div>
        </Panel>
        <Panel title="Consommation du jour">
          {u ? <KV rows={[['Appels', `${u.calls} (${u.errors} erreur(s))`], ['Jetons', `${u.tokens.toLocaleString('fr-FR')} / ${u.token_limit?.toLocaleString('fr-FR') ?? '∞'}`], ['Coût', `${usd(u.cost_usd, 4)} / ${u.cost_limit_usd ? usd(u.cost_limit_usd) : '∞'}`], ['Dernière erreur', stats.data?.last_error ?? 'aucune']]} /> : <Empty title="Aucune donnée" />}
        </Panel>
      </div>
      <Panel title="Limites">
        <Row title="Résumés IA activés" hint="Désactivé : les agents fonctionnent sans résumé, rien d’autre ne change."><Switch checked={!!vals.llm_enabled} onChange={(v) => save({ llm_enabled: v })} label="Résumés IA" /></Row>
        <Row title="Limite de coût journalière ($)" hint="Au-delà, les résumés sont suspendus jusqu’au lendemain (UTC)."><NumberSetting k="llm_daily_cost_limit_usd" vals={vals} save={save} /></Row>
        <Row title="Limite de jetons journalière"><NumberSetting k="llm_daily_token_limit" vals={vals} save={save} /></Row>
        <Row title="Délai d’attente d’un appel (s)"><NumberSetting k="llm_timeout_s" vals={vals} save={save} /></Row>
      </Panel>
      <Panel title="Modèle par agent" flush>
        <Async state={agents}>{(d) => <DataTable rows={d.items.filter((a) => a.uses_llm)} rowKey={(a) => a.id} empty={<Empty title="Aucun agent n’utilise l’IA" />} cols={[
          { key: 'a', header: 'Agent', render: (a) => a.name },
          { key: 'm', header: 'Modèle', render: (a) => <ModelCell value={a.config.model ?? ''} placeholder={models.data?.default ?? s?.default_model ?? 'par défaut'} items={models.data?.items ?? []} onSave={(m) => assign(a.id, m)} /> },
        ]} />}</Async>
      </Panel>
      <Panel title="Appels récents" flush>
        <Async state={stats}>{(d) => <DataTable rows={d.recent} rowKey={(r: any) => r.ts + r.agent} empty={<Empty title="Aucun appel" hint="Les appels IA (résumés optionnels) sont listés ici avec latence, jetons et coût." />} cols={[
          { key: 't', header: 'Heure', render: (r: any) => dateTime(r.ts) },
          { key: 'a', header: 'Agent', render: (r: any) => r.agent },
          { key: 'm', header: 'Modèle', hideSm: true, render: (r: any) => r.model },
          { key: 's', header: 'Résultat', render: (r: any) => <span title={r.error ?? undefined}><Pill kind={r.ok ? 'ok' : 'err'}>{r.ok ? 'ok' : 'erreur'}</Pill></span> },
          { key: 'l', header: 'Latence', num: true, render: (r: any) => ms(r.latency_ms) },
          { key: 'k', header: 'Jetons', num: true, render: (r: any) => r.tokens },
          { key: 'c', header: 'Coût', num: true, render: (r: any) => usd(r.cost_usd, 5) },
        ]} />}</Async>
      </Panel>
      {del && <Confirm title="Supprimer la clé ?" danger confirmLabel="Supprimer" message="Les résumés IA seront désactivés jusqu’à l’enregistrement d’une nouvelle clé." onConfirm={async () => { await api('/api/llm/key', { method: 'DELETE' }); status.reload(); }} onClose={() => setDel(false)} />}
    </div>
  );
}
function ModelCell({ value, placeholder, items, onSave }: { value: string; placeholder: string; items: { id: string }[]; onSave: (m: string) => void }) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  return <div className="row"><input className="input" style={{ minWidth: 240 }} list="agent-models" aria-label="Modèle" placeholder={placeholder} value={v} onChange={(e) => setV(e.target.value)} onBlur={() => v !== value && onSave(v.trim())} /><datalist id="agent-models">{items.slice(0, 1000).map((m) => <option key={m.id} value={m.id} />)}</datalist></div>;
}

interface Reason { code: string; label: string; source: string; source_label: string }
function Watch({ vals, save }: { vals: Vals; save: (p: Vals, ok?: string) => void }) {
  const toast = useToast();
  const { instruments } = useMarket();
  const wl = useApi<{ items: any[] }>('/api/watchlist');
  const rules = useApi<{ items: any[] }>('/api/alerts/rules');
  const reasons = useApi<{ items: Reason[] }>('/api/watchlist/reasons');
  const [sym, setSym] = useState('');
  const [reason, setReason] = useState('');
  const [type, setType] = useState('above');
  const [price, setPrice] = useState('');
  const [note, setNote] = useState('');
  const [ruleSym, setRuleSym] = useState('');
  const list = reasons.data?.items ?? [];
  const picked = list.find((r) => r.code === reason);
  const originLabel = (src: string) => list.find((r) => r.source === src)?.source_label ?? src;
  const addW = async () => { try { await api('/api/watchlist', { body: { symbol: sym, reason_code: reason } }); setSym(''); setReason(''); wl.reload(); toast('success', 'Instrument ajouté à la surveillance'); } catch (e) { toast('error', 'Ajout impossible', (e as Error).message); } };
  const addR = async () => {
    const n = Number(price.replace(',', '.'));
    const rule: Record<string, unknown> = { symbol: ruleSym.toUpperCase(), type, note };
    if (type === 'pct_move') rule.pct = n; else rule.price = n;
    try { await api('/api/alerts/rules', { body: { rule } }); setPrice(''); setNote(''); rules.reload(); } catch (e) { toast('error', 'Règle refusée', (e as Error).message); }
  };
  return (
    <div className="grid c2">
      <Panel title="Alerte sur tous les actifs">
        <Row title="Surveiller tous les instruments disponibles" hint="Une notification est émise, une fois par jour et par sens, quand un instrument bouge d’au moins le seuil depuis l’ouverture du jour. Fonctionne sur les cotations en direct de tout le catalogue (BiQuote et Kraken), sans créer une règle par instrument."><Switch label="Alerte sur tous les actifs" checked={vals.alert_all_assets !== false} onChange={(v) => save({ alert_all_assets: v }, v ? 'Alerte globale activée' : 'Alerte globale désactivée')} /></Row>
        <Row title="Seuil de variation (%)" hint="Variation depuis l’ouverture du jour, à la hausse ou à la baisse."><NumberSetting k="alert_all_pct" vals={vals} save={save} /></Row>
      </Panel>
      <Panel title="Liste de surveillance" flush>
        <div className="stack" style={{ padding: 12, gap: 8 }}>
          <div className="row" style={{ flexWrap: 'wrap' }}>
            <div style={{ flex: '1 1 220px', minWidth: 0 }}><InstrumentPicker instruments={instruments} value={sym} onChange={setSym} label="Instrument à surveiller" /></div>
            <select className="input" style={{ flex: '1 1 200px', width: 'auto' }} aria-label="Raison de la surveillance" value={reason} onChange={(e) => setReason(e.target.value)}>
              <option value="">Choisir une raison…</option>
              {list.map((r) => <option key={r.code} value={r.code}>{r.label}</option>)}
            </select>
            <button className="btn primary" disabled={!sym || !reason} onClick={addW}>Ajouter</button>
          </div>
          <span className="muted">{picked ? <>Origine : <b>{picked.source_label}</b> (définie automatiquement selon la raison)</> : 'L’origine est définie automatiquement selon la raison choisie.'}</span>
          {!instruments.length && <span className="warn">Aucun instrument disponible : le catalogue de marchés n’est pas encore chargé.</span>}
        </div>
        <Async state={wl}>{(d) => <DataTable rows={d.items} rowKey={(r) => r.symbol} empty={<Empty title="Liste vide" hint="Les instruments retenus par le Scanner s’ajoutent ici ; vous pouvez aussi en ajouter à la main." />} cols={[
          { key: 's', header: 'Instrument', render: (r) => <span className="sym">{r.symbol}</span> },
          { key: 'o', header: 'Origine', render: (r) => originLabel(r.source) },
          { key: 'r', header: 'Raison', hideSm: true, render: (r) => <span className="muted">{r.reason}</span> },
          { key: 'x', header: '', render: (r) => <button className="btn sm ghost" aria-label={`Retirer ${r.symbol}`} onClick={async () => { await api(`/api/watchlist/${r.symbol}`, { method: 'DELETE' }); wl.reload(); }}>Retirer</button> },
        ]} />}</Async>
      </Panel>
      <Panel title="Alertes de prix" flush>
        <div className="row" style={{ padding: 12 }}>
          <input className="input" style={{ width: 110 }} placeholder="Instrument" aria-label="Instrument de l’alerte" value={ruleSym} onChange={(e) => setRuleSym(e.target.value.toUpperCase())} />
          <select className="input" style={{ width: 150 }} aria-label="Type d’alerte" value={type} onChange={(e) => setType(e.target.value)}><option value="above">Au-dessus de</option><option value="below">En dessous de</option><option value="pct_move">Variation de (%)</option></select>
          <input className="input" style={{ width: 110 }} inputMode="decimal" placeholder={type === 'pct_move' ? '%' : 'Prix'} aria-label="Valeur" value={price} onChange={(e) => setPrice(e.target.value)} />
          <input className="input" style={{ width: 150 }} placeholder="Note" aria-label="Note" value={note} onChange={(e) => setNote(e.target.value)} />
          <button className="btn primary" disabled={!ruleSym || !price.trim()} onClick={addR}>Ajouter</button>
        </div>
        <Async state={rules}>{(d) => <DataTable rows={d.items} rowKey={(r) => r.id} empty={<Empty title="Aucune alerte" hint="Les alertes de stop, de cible et d’heure sont créées automatiquement pour chaque position ouverte par un agent." />} cols={[
          { key: 'r', header: 'Règle', render: (r) => `${r.rule.symbol ?? ''} ${r.rule.type} ${r.rule.price ?? r.rule.pct ?? r.rule.at ?? ''}` },
          { key: 'n', header: 'Note', hideSm: true, render: (r) => <span className="muted">{r.rule.note ?? ''}</span> },
          { key: 'a', header: 'Active', render: (r) => <Switch checked={r.active} onChange={async (v) => { await api(`/api/alerts/rules/${r.id}?active=${v}`, { method: 'PATCH', body: {} }); rules.reload(); }} label={`Activer la règle ${r.id}`} /> },
          { key: 'x', header: '', render: (r) => <button className="btn sm ghost" aria-label={`Supprimer la règle ${r.id}`} onClick={async () => { await api(`/api/alerts/rules/${r.id}`, { method: 'DELETE' }); rules.reload(); }}>Supprimer</button> },
        ]} />}</Async>
      </Panel>
    </div>
  );
}

function Misc() {
  const { mode, setMode } = useTheme();
  const toast = useToast();
  const sec = useApi<{ sec_user_agent_configured: boolean; sec_user_agent_masked: string }>('/api/settings/secrets');
  const meta = useApi<any>('/api/meta');
  const [ua, setUa] = useState('');
  const saveUa = async () => { try { await api('/api/settings/secrets', { method: 'PUT', body: { sec_user_agent: ua } }); setUa(''); toast('success', 'User-Agent SEC enregistré'); sec.reload(); } catch (e) { toast('error', 'Refusé', (e as Error).message); } };
  return (
    <div className="grid c2">
      <Panel title="Apparence"><Row title="Thème" hint="« Système » suit le réglage de votre ordinateur."><Segmented label="Thème" value={mode} onChange={setMode} options={[{ value: 'light', label: 'Clair' }, { value: 'dark', label: 'Sombre' }, { value: 'system', label: 'Système' }]} /></Row></Panel>
      <Panel title="SEC EDGAR (Analyst, News)">
        <div className="stack">
          <p className="muted">La SEC exige un User-Agent identifiant le demandeur : votre nom et votre e-mail, par exemple « Prénom Nom nom@exemple.com ». Sans lui, les données SEC sont indisponibles.</p>
          <div className="row"><Pill kind={sec.data?.sec_user_agent_configured ? 'ok' : 'warn'}>{sec.data?.sec_user_agent_configured ? `Configuré ${sec.data.sec_user_agent_masked}` : 'Non configuré'}</Pill></div>
          <div className="row"><input className="input" style={{ maxWidth: 360 }} aria-label="User-Agent SEC" placeholder="Prénom Nom nom@exemple.com" value={ua} onChange={(e) => setUa(e.target.value)} /><button className="btn primary" disabled={!ua.trim()} onClick={saveUa}>Enregistrer</button></div>
        </div>
      </Panel>
      <SystemNotifications />
      <UpdatesPanel />
      <Panel title="À propos"><Async state={meta}>{(m) => <KV rows={[['Application', `${m.app} ${m.version}`], ['Mode', 'Paper trading uniquement'], ['Source des marchés', m.provider], ['Modèle IA par défaut', m.default_model], ['Skills', `${m.provenance.repo} @ ${String(m.provenance.commit).slice(0, 7)}`], ['Licence des skills', m.provenance.license], ['Graphiques', 'Lightweight Charts™, © TradingView, Inc. (licence Apache 2.0)']]} />}</Async></Panel>
    </div>
  );
}

function SystemNotifications() {
  const toast = useToast();
  const [on, setOn] = useState(desktopNotifEnabled());
  const [perm, setPerm] = useState(webNotifPermission());
  const desk = !!window.desktop?.notify;
  const test = async () => {
    if (!desk && perm !== 'granted') {
      setPerm(await requestWebNotifPermission());
      if (webNotifPermission() !== 'granted') { toast('warning', 'Notifications refusées', 'Autorisez-les dans les réglages de votre navigateur pour cette page.'); return; }
    }
    const ok = await sendSystemNotification({ level: 'success', title: 'Monkora', body: 'Les notifications du bureau fonctionnent.' }, { force: true });
    if (!ok) toast('warning', 'Notification non affichée', 'Vérifiez que les notifications du système ne sont pas coupées (mode Ne pas déranger, Assistant de concentration).');
  };
  return (
    <Panel title="Notifications du bureau">
      <Row title="Notifier sur le bureau" hint={desk ? 'Quand la fenêtre est réduite, masquée ou en arrière-plan, chaque alerte et chaque ordre apparaît dans les notifications de votre système. Fermer la fenêtre garde Monkora actif dans la zone de notification.' : 'Quand cet onglet est en arrière-plan, les alertes s’affichent dans les notifications de votre système. Pour les recevoir application fermée, utilisez l’application de bureau.'}>
        <Switch checked={on} onChange={(v) => { setOn(v); setDesktopNotifEnabled(v); }} label="Notifications du bureau" />
      </Row>
      <div className="row" style={{ paddingTop: 10 }}>
        {!desk && perm === 'default' && <button className="btn" onClick={async () => setPerm(await requestWebNotifPermission())}>Autoriser les notifications</button>}
        {!desk && perm === 'denied' && <Pill kind="warn">Refusées par le navigateur</Pill>}
        {!desk && perm === 'unsupported' && <Pill kind="mute">Non prises en charge par ce navigateur</Pill>}
        <button className="btn" onClick={test}>Tester</button>
      </div>
    </Panel>
  );
}

function UpdatesPanel() {
  const u = useUpdate();
  const info = u.info;
  return (
    <Panel title="Mises à jour">
      <div className="stack">
        <KV rows={[['Version installée', info?.current ?? '…'], ['Dernière version', info?.latest?.version ?? (u.checking ? 'Vérification…' : 'Inconnue')]]} />
        <div className="row">
          {info?.update_available ? <Pill kind="warn">Mise à jour disponible</Pill> : info && !info.error ? <Pill kind="ok">À jour</Pill> : null}
          <button className="btn" disabled={u.checking} onClick={() => u.check(true)}>Vérifier maintenant</button>
          {info?.update_available && <button className="btn primary" onClick={() => u.setOpen(true)}>Mettre à jour</button>}
        </div>
        {info?.error && <Banner kind="warn">Impossible de joindre la liste des releases : {info.error}</Banner>}
        {info && info.releases.length > 0 && (
          <table className="tbl" aria-label="Liste des versions">
            <thead><tr><th>Version</th><th>Date</th><th /></tr></thead>
            <tbody>
              {info.releases.map((r) => (
                <tr key={r.tag}><td>{r.version}{r.version === info.current && ' (installée)'}</td><td>{r.published_at ? dateTime(r.published_at) : ''}</td><td><a href={r.url} target="_blank" rel="noopener noreferrer">Notes</a></td></tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </Panel>
  );
}
