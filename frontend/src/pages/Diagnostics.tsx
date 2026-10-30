import { Link } from 'react-router-dom';
import { Async, DataTable, Empty, KV, Page, Panel, Pill } from '../components/ui';
import { useApi } from '../lib/hooks';
import { useConnState } from '../lib/live';
import { age, dateTime, ms } from '../lib/format';

const LV: Record<string, 'ok' | 'warn' | 'err' | 'mute'> = { ok: 'ok', warning: 'warn', error: 'err' };
const LABEL: Record<string, string> = { ok: 'OK', warning: 'À vérifier', error: 'Erreur' };

export default function Diagnostics() {
  const d = useApi<any>('/api/diagnostics', 10000);
  const ws = useConnState();
  return (
    <Page title="Diagnostic du système" subtitle="État réel de chaque composant. Un composant non configuré est signalé « à vérifier », jamais présenté comme fonctionnel." actions={<button className="btn" onClick={d.reload}>Actualiser</button>}>
      <Async state={d}>
        {(x) => (
          <>
            <Panel title="Vérifications" flush>
              <DataTable rows={[{ name: 'Connexion temps réel de l’interface', level: ws === 'open' ? 'ok' : 'error', detail: ws === 'open' ? 'WebSocket connectée' : `WebSocket ${ws}` }, ...x.checks]} rowKey={(r: any) => r.name} cols={[
                { key: 'n', header: 'Composant', render: (r: any) => <b>{r.name}</b> },
                { key: 'l', header: 'État', render: (r: any) => <Pill kind={LV[r.level] ?? 'mute'}>{LABEL[r.level] ?? r.level}</Pill> },
                { key: 'd', header: 'Détail', render: (r: any) => <span className="muted">{r.detail}</span> },
              ]} />
            </Panel>
            <div className="grid c2">
              <Panel title="Flux BiQuote"><KV rows={[['Connexion', x.feed.stream?.state], ['Dernier tick', x.feed.stream?.last_tick_at ? dateTime(x.feed.stream.last_tick_at) : '—'], ['Ticks reçus / invalides', `${x.feed.stream?.ticks_total ?? 0} / ${x.feed.stream?.ticks_invalid ?? 0}`], ['Débit', `${x.feed.stream?.ticks_per_second ?? 0} ticks/s`], ['Reconnexions', x.feed.stream?.reconnects], ['Instruments abonnés', `${x.feed.stream?.subscribed ?? 0} / ${x.feed.stream?.requested ?? 0}`], ['Dernière erreur', x.feed.stream?.last_error ?? 'aucune'], ['Instruments', x.feed.instruments], ['Cotations', x.feed.quotes], ['En direct / périmées / fermées', `${x.feed.live} / ${x.feed.stale} / ${x.feed.closed}`], ['Seuil « en direct »', `${x.feed.live_threshold_s} s`], ['Mode d’abonnement', x.feed.subscribe_mode], ['Erreur de catalogue', x.feed.catalogue_error]]} /></Panel>
              <Panel title="Orchestrateur"><KV rows={[['Pipeline', x.pipeline.pipeline_enabled ? 'actif' : 'en pause'], ['Exécution automatique', x.pipeline.auto_execute ? 'oui' : 'non (approbation manuelle)'], ['Interrupteur d’arrêt', x.pipeline.kill_switch ? 'ACTIF' : 'inactif'], ['Tâches planifiées', x.pipeline.tasks], ['Démarré', dateTime(x.pipeline.started_at)], ['Surveillance', `${x.pipeline.watchlist.length} instrument(s)`], ['Exécutions en cours', Object.keys(x.pipeline.running).length], ['Clients WebSocket', x.ws_clients], ['Heure du serveur', dateTime(x.server_time)]]} /></Panel>
            </div>
            <Panel title="Dernière exécution de chaque agent" flush>
              <DataTable rows={x.agents.map((a: string) => ({ id: a, run: x.pipeline.last_runs[a] }))} rowKey={(r: any) => r.id} cols={[
                { key: 'a', header: 'Agent', render: (r: any) => <Link to={`/agents/${r.id}`}>{r.id}</Link> },
                { key: 's', header: 'Statut', render: (r: any) => (r.run ? <Pill kind={r.run.status === 'ok' ? 'ok' : r.run.status === 'error' ? 'err' : 'warn'}>{r.run.status}</Pill> : <Pill kind="mute">jamais</Pill>) },
                { key: 'w', header: 'Il y a', num: true, render: (r: any) => (r.run ? age((Date.now() - new Date(r.run.at).getTime()) / 1000) : '—') },
                { key: 'd', header: 'Durée', num: true, hideSm: true, render: (r: any) => (r.run ? ms(r.run.duration_ms) : '—') },
                { key: 'r', header: 'Résumé', render: (r: any) => <span className="muted">{r.run?.summary}</span> },
              ]} />
            </Panel>
            <div className="grid c2">
              <Panel title="Erreurs des dernières 24 h" flush><DataTable rows={x.recent_errors} rowKey={(r: any) => r.at + r.agent} empty={<Empty title="Aucune erreur d’agent" />} cols={[
                { key: 't', header: 'Heure', render: (r: any) => dateTime(r.at) }, { key: 'a', header: 'Agent', render: (r: any) => r.agent }, { key: 'e', header: 'Erreur', render: (r: any) => <span className="down">{r.error}</span> },
              ]} /></Panel>
              <Panel title="IA (OpenRouter)"><KV rows={[['Appels aujourd’hui', x.llm.today.calls], ['Erreurs', x.llm.today.errors], ['Coût', `${x.llm.today.cost_usd.toFixed(4)} $`], ['Dernière erreur', x.llm.last_error ?? 'aucune'], ['Dernier succès', x.llm.last_ok_at ? dateTime(x.llm.last_ok_at) : '—']]} /></Panel>
            </div>
            <Panel title="Origine des skills"><KV rows={[['Dépôt', x.provenance.repo], ['Commit de référence', x.provenance.commit], ['Licence', x.provenance.license]]} /></Panel>
          </>
        )}
      </Async>
    </Page>
  );
}
