import { useState } from 'react';
import { Async, DataTable, Empty, Page, Panel, Pill, Segmented } from '../components/ui';
import { api } from '../lib/api';
import { useApi } from '../lib/hooks';
import { useLiveEvent } from '../lib/live';
import { dateTime } from '../lib/format';

const LV: Record<string, 'ok' | 'warn' | 'err' | 'info'> = { success: 'ok', warning: 'warn', error: 'err', info: 'info' };
const LABEL: Record<string, string> = { success: 'Succès', warning: 'Alerte', error: 'Erreur', info: 'Info' };

export default function Notifications() {
  const [only, setOnly] = useState<'all' | 'unread'>('all');
  const [level, setLevel] = useState('all');
  const st = useApi<{ unread: number; items: any[] }>(`/api/notifications?limit=300${only === 'unread' ? '&unread=true' : ''}`, 10000);
  useLiveEvent('notification', () => st.reload());
  const markAll = async () => { await api('/api/notifications/read', { body: [] }); st.reload(); };
  const markOne = async (id: number) => { await api('/api/notifications/read', { body: [id] }); st.reload(); };
  return (
    <Page title="Notifications" subtitle="Signaux, ordres, alertes de prix et incidents techniques (flux de données, IA, interrupteur d’arrêt)."
      actions={<><Segmented label="Lecture" value={only} onChange={setOnly} options={[{ value: 'all', label: 'Toutes' }, { value: 'unread', label: `Non lues${st.data?.unread ? ` (${st.data.unread})` : ''}` }]} /><Segmented label="Niveau" value={level} onChange={setLevel} options={[{ value: 'all', label: 'Tous' }, { value: 'error', label: 'Erreurs' }, { value: 'warning', label: 'Alertes' }]} /><button className="btn" onClick={markAll} disabled={!st.data?.unread}>Tout marquer comme lu</button></>}>
      <Panel flush>
        <Async state={st}>{(d) => <DataTable rows={d.items.filter((n) => level === 'all' || n.level === level)} rowKey={(n) => n.id} onRow={(n) => !n.read && void markOne(n.id)} empty={<Empty title="Aucune notification" />} cols={[
          { key: 't', header: 'Heure', render: (n) => dateTime(n.ts) },
          { key: 'l', header: 'Niveau', render: (n) => <Pill kind={LV[n.level] ?? 'info'}>{LABEL[n.level] ?? n.level}</Pill> },
          { key: 's', header: 'Source', hideSm: true, render: (n) => n.source },
          { key: 'i', header: 'Instrument', hideSm: true, render: (n) => n.symbol ?? '—' },
          { key: 'x', header: 'Message', render: (n) => <span style={{ fontWeight: n.read ? 400 : 600 }}>{n.title}{n.body ? <span className="muted" style={{ fontWeight: 400 }}> — {n.body}</span> : null}</span> },
        ]} />}</Async>
      </Panel>
    </Page>
  );
}
