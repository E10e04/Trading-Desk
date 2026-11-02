import { useState } from 'react';
import { Async, Banner, BlockTitle, Confirm, DataTable, Empty, JsonBlock, KV, Modal, Page, Panel, Pill, Segmented, Tabs, useToast } from '../components/ui';
import { OrderModal } from '../components/OrderModal';
import { PortfolioSelect } from '../components/PortfolioSelect';
import { api } from '../lib/api';
import { useApi } from '../lib/hooks';
import { useLiveEvent } from '../lib/live';
import { usePortfolios } from '../lib/portfolio';
import { dateTime, price, qty, signed, tone } from '../lib/format';

type Tab = 'props' | 'orders' | 'fills' | 'ledger';
const ST: Record<string, 'ok' | 'warn' | 'err' | 'mute' | 'info'> = { filled: 'ok', executed: 'ok', open: 'info', triggered: 'info', pending: 'warn', rejected: 'err', blocked: 'err', cancelled: 'mute', expired: 'mute', failed: 'err' };

export default function Orders() {
  const toast = useToast();
  const pf = usePortfolios(0, true);
  const [tab, setTab] = useState<Tab>('props');
  const [filter, setFilter] = useState<'all' | 'active'>('all');
  const [newOrder, setNewOrder] = useState(false);
  const [detail, setDetail] = useState<any>(null);
  const [cancel, setCancel] = useState<any>(null);
  const q = pf.sel > 0 ? `portfolio_id=${pf.sel}` : '';
  const props = useApi<{ items: any[] }>('/api/proposals?limit=200', 5000);
  const orders = useApi<{ items: any[] }>(`/api/orders?${q}${filter === 'active' ? '&status=active' : ''}`, 4000);
  const fills = useApi<{ items: any[] }>(tab === 'fills' ? `/api/fills?${q}` : null, 6000);
  const ledger = useApi<{ items: any[] }>(tab === 'ledger' ? `/api/ledger?${q}` : null, 8000);
  const pipe = useApi<{ auto_execute: boolean }>('/api/pipeline/status', 10000);
  const enableAuto = async () => { try { await api('/api/settings', { method: 'PUT', body: { auto_execute: true } }); toast('success', 'Exécution automatique activée'); pipe.reload(); props.reload(); } catch (e) { toast('error', 'Réglage refusé', (e as Error).message); } };
  useLiveEvent('order', () => orders.reload());
  useLiveEvent('fill', () => { orders.reload(); fills.reload(); });
  useLiveEvent('proposal', () => props.reload());
  const names = Object.fromEntries(pf.items.map((p) => [p.id, p.name]));
  const decide = async (p: any, reject: boolean) => {
    try {
      const r = await api<any>(`/api/proposals/${p.id}/decide`, { body: reject ? { reject: true } : { reply: 'yes' } });
      toast(reject ? 'info' : r.status === 'executed' ? 'success' : 'warning', reject ? 'Proposition refusée' : `Proposition : ${r.status}`, r.note);
      props.reload(); orders.reload();
    } catch (e) { toast('error', 'Décision impossible', (e as Error).message); }
  };
  const rows = (props.data?.items ?? []).filter((p) => pf.sel <= 0 || p.portfolio_id === pf.sel);
  return (
    <Page title="Ordres" subtitle="Propositions des agents, ordres papier, exécutions et journal du moteur. Toute proposition passe par l’Agent Risk avant d’atteindre le Broker."
      actions={<><PortfolioSelect items={pf.items} value={pf.sel} onChange={pf.setSel} all /><button className="btn primary" onClick={() => setNewOrder(true)} disabled={!pf.items.length}>Ordre manuel</button></>}>
      {pipe.data && !pipe.data.auto_execute && (
        <Banner kind="warn"><div className="row between" style={{ gap: 12, flexWrap: 'wrap' }}><span><b>Exécution automatique désactivée.</b> C’est pour cela que des propositions attendent votre approbation : tant que ce réglage est coupé, aucun ordre n’est pris sans votre accord (une proposition expire après 15 min). Il se règle dans Réglages › Exécution.</span><button className="btn sm primary" onClick={enableAuto}>Activer l’exécution automatique</button></div></Banner>
      )}
      <Tabs value={tab} onChange={setTab} tabs={[{ id: 'props', label: 'Propositions' }, { id: 'orders', label: 'Ordres' }, { id: 'fills', label: 'Exécutions' }, { id: 'ledger', label: 'Journal du moteur' }]} />
      {tab === 'props' && (
        <Panel flush>
          <Async state={props}>{() => <DataTable rows={rows} rowKey={(r) => r.id} onRow={setDetail} empty={<Empty title="Aucune proposition" hint="Une proposition est enregistrée à chaque signal d’entrée, qu’elle soit exécutée, bloquée par Risk ou refusée." />} cols={[
            { key: 't', header: 'Heure', render: (r) => dateTime(r.ts) },
            { key: 'p', header: 'Portefeuille', hideSm: true, render: (r) => names[r.portfolio_id] ?? r.portfolio_id },
            { key: 's', header: 'Instrument', render: (r) => <span className="sym">{r.symbol}</span> },
            { key: 'q', header: 'Quantité', num: true, hideSm: true, render: (r) => qty(r.sizing?.qty) },
            { key: 'k', header: 'Risk', render: (r) => (r.risk?.verdict ? <Pill kind={r.risk.verdict === 'PASS' ? 'ok' : 'err'}>{r.risk.verdict}</Pill> : '—') },
            { key: 'd', header: 'Décision', hideSm: true, render: (r) => (r.decision === 'auto' ? 'automatique' : r.decision ?? '—') },
            { key: 'st', header: 'Statut', render: (r) => <Pill kind={ST[r.status] ?? 'mute'}>{r.status}</Pill> },
            { key: 'a', header: '', render: (r) => r.status === 'pending' ? <span className="row" onClick={(e) => e.stopPropagation()}><button className="btn sm primary" onClick={() => decide(r, false)}>Approuver</button><button className="btn sm" onClick={() => decide(r, true)}>Refuser</button></span> : null },
          ]} />}</Async>
        </Panel>
      )}
      {tab === 'orders' && (
        <Panel flush actions={<Segmented label="Filtre" value={filter} onChange={setFilter} options={[{ value: 'all', label: 'Tous' }, { value: 'active', label: 'Actifs' }]} />} title="Ordres papier">
          <Async state={orders}>{(d) => <DataTable rows={d.items} rowKey={(r) => r.id} empty={<Empty title="Aucun ordre" />} cols={[
            { key: 'id', header: '#', render: (r) => r.id },
            { key: 't', header: 'Créé', render: (r) => dateTime(r.created_at) },
            { key: 'p', header: 'Portefeuille', hideSm: true, render: (r) => names[r.portfolio_id] ?? r.portfolio_id },
            { key: 's', header: 'Instrument', render: (r) => <span className="sym">{r.symbol}</span> },
            { key: 'sd', header: 'Sens', render: (r) => (r.side === 'buy' ? 'Achat' : 'Vente') },
            { key: 'ty', header: 'Type', hideSm: true, render: (r) => `${r.type}${r.role && r.role !== 'standalone' ? ` · ${r.role}` : ''}` },
            { key: 'q', header: 'Quantité', num: true, render: (r) => `${qty(r.filled_qty)} / ${qty(r.qty)}` },
            { key: 'pr', header: 'Prix', num: true, render: (r) => price(r.avg_fill_price ?? r.limit_price ?? r.stop_price) },
            { key: 'so', header: 'Source', hideSm: true, render: (r) => r.source },
            { key: 'st', header: 'Statut', render: (r) => <span title={r.reject_reason ?? undefined}><Pill kind={ST[r.status] ?? 'mute'}>{r.status}</Pill></span> },
            { key: 'a', header: '', render: (r) => (['open', 'triggered'].includes(r.status) ? <button className="btn sm" onClick={() => setCancel(r)}>Annuler</button> : null) },
          ]} />}</Async>
        </Panel>
      )}
      {tab === 'fills' && (
        <Panel flush title="Exécutions"><Async state={fills}>{(d) => <DataTable rows={d.items} rowKey={(r) => r.id} empty={<Empty title="Aucune exécution" />} cols={[
          { key: 't', header: 'Heure', render: (r) => dateTime(r.ts) },
          { key: 's', header: 'Instrument', render: (r) => <span className="sym">{r.symbol}</span> },
          { key: 'sd', header: 'Sens', render: (r) => (r.side === 'buy' ? 'Achat' : 'Vente') },
          { key: 'q', header: 'Quantité', num: true, render: (r) => qty(r.qty) },
          { key: 'p', header: 'Prix', num: true, render: (r) => price(r.price) },
          { key: 'b', header: 'Bid / Ask', num: true, hideSm: true, render: (r) => `${price(r.bid)} / ${price(r.ask)}` },
          { key: 'sl', header: 'Glissement', num: true, hideSm: true, render: (r) => price(r.slippage) },
          { key: 'f', header: 'Frais', num: true, render: (r) => price(r.fee, 4) },
          { key: 'r', header: 'P&L réalisé', num: true, render: (r) => <span className={tone(r.realized_pnl)}>{signed(r.realized_pnl)}</span> },
        ]} />}</Async></Panel>
      )}
      {tab === 'ledger' && (
        <Panel flush title="Journal du moteur (reconstitue l’état du portefeuille)"><Async state={ledger}>{(d) => <DataTable rows={d.items} rowKey={(r) => r.id} empty={<Empty title="Journal vide" />} maxHeight={560} cols={[
          { key: 't', header: 'Heure', render: (r) => dateTime(r.ts) },
          { key: 'k', header: 'Type', render: (r) => r.kind },
          { key: 'r', header: 'Réf.', num: true, render: (r) => r.ref_id ?? '—' },
          { key: 'p', header: 'Détail', render: (r) => <code className="muted">{JSON.stringify(r.payload).slice(0, 140)}</code> },
        ]} />}</Async></Panel>
      )}
      {newOrder && <OrderModal onClose={() => setNewOrder(false)} onDone={() => orders.reload()} />}
      {cancel && <Confirm title="Annuler l’ordre ?" confirmLabel="Annuler l’ordre" message={`Ordre #${cancel.id} — ${cancel.side === 'buy' ? 'achat' : 'vente'} ${qty(cancel.qty)} ${cancel.symbol}.`} onConfirm={async () => { try { await api(`/api/orders/${cancel.id}`, { method: 'DELETE' }); orders.reload(); } catch (e) { toast('error', 'Annulation impossible', (e as Error).message); } }} onClose={() => setCancel(null)} />}
      {detail && (
        <Modal title={`Proposition #${detail.id} — ${detail.symbol}`} onClose={() => setDetail(null)}>
          <div className="stack">
            {detail.note && <Banner kind={detail.status === 'executed' ? 'info' : 'warn'}>{detail.note}</Banner>}
            <BlockTitle title="Résumé" text={[`Statut : ${detail.status}`, `Décision : ${detail.decision}`, `Setup : ${detail.setup}`, `Verdict Risk : ${detail.risk?.verdict ?? '—'}`, `Raisons : ${(detail.risk?.reasons ?? []).join(' · ') || '—'}`, `Corrélation : ${detail.correlation_id}`].join('\n')} />
            <KV rows={[['Statut', detail.status], ['Décision', detail.decision === 'auto' ? 'automatique (politique auto_execute)' : detail.decision], ['Setup', detail.setup], ['Verdict Risk', detail.risk?.verdict], ['Raisons', (detail.risk?.reasons ?? []).join(' · ') || '—'], ['Corrélation', detail.correlation_id]]} />
            <BlockTitle title="Plan" text={JSON.stringify(detail.plan, null, 2)} /><JsonBlock data={detail.plan} />
            <BlockTitle title="Dimensionnement" text={JSON.stringify(detail.sizing, null, 2)} /><JsonBlock data={detail.sizing} />
            <BlockTitle title="Contrôle Risk" text={JSON.stringify(detail.risk, null, 2)} /><JsonBlock data={detail.risk} />
          </div>
        </Modal>
      )}
    </Page>
  );
}
