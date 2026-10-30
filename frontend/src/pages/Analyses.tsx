import { useState } from 'react';
import { Async, Banner, DataTable, Empty, Field, JsonBlock, Modal, Page, Panel, Pill, Segmented, Tabs, useToast } from '../components/ui';
import { api } from '../lib/api';
import { useApi } from '../lib/hooks';
import { dateTime } from '../lib/format';

type Tab = 'analyses' | 'journal';
const LABEL: Record<string, string> = { analyst: 'Analyst', news: 'News', pelosi: 'Pelosi', timemachine: 'TimeMachine' };

export default function Analyses() {
  const toast = useToast();
  const [tab, setTab] = useState<Tab>('analyses');
  const [agent, setAgent] = useState('all');
  const [symbol, setSymbol] = useState('');
  const [open, setOpen] = useState<any>(null);
  const an = useApi<{ items: any[] }>(`/api/analyses?limit=100${symbol ? `&symbol=${symbol}` : ''}`, 15000);
  const jr = useApi<{ items: any[] }>('/api/journal', 0);
  const [text, setText] = useState('');
  const [sym, setSym] = useState('');
  const [tags, setTags] = useState('');
  const add = async () => {
    try { await api('/api/journal', { body: { text, symbol: sym || null, tags: tags.split(',').map((t) => t.trim()).filter(Boolean) } }); setText(''); setTags(''); jr.reload(); }
    catch (e) { toast('error', 'Note refusée', (e as Error).message); }
  };
  const items = (an.data?.items ?? []).filter((x) => agent === 'all' || x.agent === agent);
  return (
    <Page title="Analyses et journal" subtitle="Résultats des agents d’analyse (Analyst, News, Pelosi, TimeMachine) et notes personnelles.">
      <Tabs value={tab} onChange={setTab} tabs={[{ id: 'analyses', label: 'Analyses des agents' }, { id: 'journal', label: 'Journal personnel' }]} />
      {tab === 'analyses' && (
        <Panel flush title="Analyses" actions={<><input className="input" style={{ width: 130 }} placeholder="Instrument" aria-label="Filtrer par instrument" value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} /><Segmented label="Agent" value={agent} onChange={setAgent} options={[{ value: 'all', label: 'Tous' }, ...Object.entries(LABEL).map(([v, label]) => ({ value: v, label }))]} /></>}>
          <Async state={an}>{() => <DataTable rows={items} rowKey={(r) => r.id} onRow={setOpen} empty={<Empty title="Aucune analyse" hint="Les analyses apparaissent après l’exécution des agents News, Analyst, Pelosi ou TimeMachine." />} cols={[
            { key: 't', header: 'Date', render: (r) => dateTime(r.at) },
            { key: 'a', header: 'Agent', render: (r) => LABEL[r.agent] ?? r.agent },
            { key: 'i', header: 'Instrument', render: (r) => r.instrument ?? '—' },
            { key: 's', header: 'Statut', render: (r) => <Pill kind={r.status === 'ok' ? 'ok' : 'warn'}>{r.status === 'ok' ? 'complet' : 'partiel'}</Pill> },
            { key: 'r', header: 'Résumé', render: (r) => <span className="muted">{r.summary}</span> },
          ]} />}</Async>
        </Panel>
      )}
      {tab === 'journal' && (
        <div className="grid side">
          <Panel flush title="Notes"><Async state={jr}>{(d) => <DataTable rows={d.items} rowKey={(r) => r.id} empty={<Empty title="Journal vide" hint="Notez vos observations, avec un instrument et des étiquettes pour les retrouver." />} cols={[
            { key: 't', header: 'Date', render: (r) => dateTime(r.ts) },
            { key: 'i', header: 'Instrument', render: (r) => r.symbol ?? '—' },
            { key: 'x', header: 'Note', render: (r) => <span style={{ whiteSpace: 'pre-wrap' }}>{r.text}</span> },
            { key: 'g', header: 'Étiquettes', hideSm: true, render: (r) => (r.tags ?? []).map((g: string) => <Pill key={g} kind="mute" plain>{g}</Pill>) },
            { key: 'd', header: '', render: (r) => <button className="btn sm ghost" aria-label="Supprimer la note" onClick={async () => { await api(`/api/journal/${r.id}`, { method: 'DELETE' }); jr.reload(); }}>Supprimer</button> },
          ]} />}</Async></Panel>
          <Panel title="Nouvelle note" actions={<button className="btn primary" disabled={!text.trim()} onClick={add}>Ajouter</button>}>
            <div className="stack">
              <Field label="Note">{(id) => <textarea id={id} className="input" style={{ minHeight: 120 }} value={text} onChange={(e) => setText(e.target.value)} />}</Field>
              <Field label="Instrument (option)">{(id) => <input id={id} className="input" value={sym} onChange={(e) => setSym(e.target.value.toUpperCase())} />}</Field>
              <Field label="Étiquettes" hint="Séparées par des virgules">{(id) => <input id={id} className="input" value={tags} onChange={(e) => setTags(e.target.value)} />}</Field>
            </div>
          </Panel>
        </div>
      )}
      {open && (
        <Modal title={`${LABEL[open.agent] ?? open.agent} — ${open.instrument ?? 'général'}`} onClose={() => setOpen(null)}>
          <div className="stack">
            {open.status !== 'ok' && <Banner kind="warn">Analyse partielle : certaines sources n’étaient pas disponibles.</Banner>}
            {open.warnings?.map((w: string) => <Banner key={w} kind="warn">{w}</Banner>)}
            <p>{open.summary}</p>
            {open.model && <span className="muted">Résumé rédigé par {open.model} à partir de faits calculés.</span>}
            <JsonBlock data={open.output} />
          </div>
        </Modal>
      )}
    </Page>
  );
}
