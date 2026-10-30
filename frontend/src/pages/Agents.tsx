import { Link } from 'react-router-dom';
import { Async, Banner, Empty, Pill, Page, Panel, Switch, useToast } from '../components/ui';
import { api } from '../lib/api';
import { useApi } from '../lib/hooks';
import { useLiveEvent } from '../lib/live';
import { IMPL, RUN_KIND } from '../lib/agents';
import { age, ms } from '../lib/format';

export default function Agents() {
  const st = useApi<{ items: any[]; provenance: any; pipeline: any }>('/api/agents', 6000);
  const toast = useToast();
  useLiveEvent('agent_run', () => st.reload());
  const toggle = async (id: string, enabled: boolean) => {
    try { await api(`/api/agents/${id}/config`, { method: 'PUT', body: { enabled } }); st.reload(); }
    catch (e) { toast('error', 'Configuration impossible', (e as Error).message); }
  };
  return (
    <Page title="Agents IA" subtitle="Onze agents, un par skill du dépôt day-trader-skill. Chaque agent est un composant autonome, configurable, testé et audité.">
      <Async state={st}>
        {(d) => (
          <>
            <Banner kind="info">
              <span>Source : <code>{d.provenance.repo}</code> au commit <code>{String(d.provenance.commit).slice(0, 7)}</code> (licence {d.provenance.license}). Les calculs (prix, quantités, risque) sont déterministes ; l’IA ne sert qu’à résumer des faits déjà calculés.</span>
            </Banner>
            <div className="grid c3">
              {d.items.map((a) => {
                const impl = IMPL[a.implementation] ?? IMPL.partial;
                const lr = a.last_run;
                return (
                  <Panel key={a.id} title={<Link to={`/agents/${a.id}`}>{a.name}</Link>} actions={<Switch checked={a.config.enabled} onChange={(v) => toggle(a.id, v)} label={`Activer ${a.name}`} />}>
                    <div className="stack" style={{ gap: 8 }}>
                      <p className="muted">{a.mission}</p>
                      <div className="row">
                        <Pill kind={impl.kind}>{impl.label}</Pill>
                        {a.uses_llm && <Pill kind="info" plain>IA optionnelle</Pill>}
                        {!a.config.enabled && <Pill kind="mute">Désactivé</Pill>}
                      </div>
                      <div className="muted" style={{ fontSize: 'var(--fs-0)' }}>
                        {lr ? <>Dernière exécution : <Pill kind={RUN_KIND[lr.status] ?? 'mute'}>{lr.status}</Pill> il y a {age((Date.now() - new Date(lr.started_at).getTime()) / 1000)} · {ms(lr.duration_ms)}</> : 'Jamais exécuté'}
                      </div>
                      {lr && <div className="muted" style={{ fontSize: 'var(--fs-0)', overflowWrap: 'anywhere' }}>{lr.summary}</div>}
                      <div className="muted" style={{ fontSize: 'var(--fs-0)' }}>
                        {a.run_counts.ok} ok · {a.run_counts.partial} partiels · {a.run_counts.skipped} ignorés · <span className={a.run_counts.error ? 'down' : ''}>{a.run_counts.error} erreurs</span>
                        {a.config.model ? ` · modèle ${a.config.model}` : ''}
                      </div>
                    </div>
                  </Panel>
                );
              })}
            </div>
            {!d.items.length && <Empty title="Aucun agent" />}
          </>
        )}
      </Async>
    </Page>
  );
}
