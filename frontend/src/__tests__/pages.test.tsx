import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../components/ui';
import { setToken } from '../lib/api';
import { applyTheme, readMode } from '../lib/theme';

type Handler = (init: RequestInit | undefined, url: string) => unknown;
function mockApi(routes: Record<string, Handler | unknown>) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) => {
    const url = String(input);
    calls.push({ url, init });
    const key = Object.keys(routes).find((k) => url.split('?')[0] === k || url.startsWith(k));
    if (!key) return Promise.resolve(new Response(JSON.stringify({ detail: `route non simulée ${url}` }), { status: 404 }));
    const r = routes[key];
    const body = typeof r === 'function' ? (r as Handler)(init, url) : r;
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } }));
  });
  return calls;
}
const wrap = (ui: React.ReactElement) => render(<MemoryRouter><ToastProvider>{ui}</ToastProvider></MemoryRouter>);

beforeEach(() => { localStorage.clear(); setToken('t'); });
afterEach(() => vi.restoreAllMocks());

describe('pages', () => {
  it('Réglages IA : la clé OpenRouter est saisie en champ masqué, envoyée au serveur puis effacée de l’écran', async () => {
    const calls = mockApi({
      '/api/settings': { values: { llm_enabled: true, llm_daily_cost_limit_usd: 1, llm_daily_token_limit: 1000, llm_timeout_s: 45 }, defaults: {} },
      '/api/llm/status': { configured: false, masked: '', source: 'none', available: false, unavailable_reason: 'aucune clé', default_model: 'minimax/minimax-m3.1-flash' },
      '/api/llm/stats': { today: { calls: 0, errors: 0, tokens: 0, cost_usd: 0, token_limit: 1000, cost_limit_usd: 1 }, by_model: [], recent: [], last_error: null },
      '/api/llm/models': { default: 'minimax/minimax-m3.1-flash', items: [{ id: 'minimax/minimax-m3.1-flash', name: 'MiniMax' }] },
      '/api/agents': { items: [], provenance: {}, pipeline: {} },
      '/api/llm/key': () => ({ configured: true, masked: 'sk-or-…abcd', source: 'file' }),
    });
    const { default: Settings } = await import('../pages/Settings');
    wrap(<Settings />);
    fireEvent.click(await screen.findByRole('tab', { name: 'Intelligence artificielle' }));
    const input = (await screen.findByLabelText('Clé OpenRouter')) as HTMLInputElement;
    expect(input.type).toBe('password');
    fireEvent.change(input, { target: { value: 'sk-or-v1-secret' } });
    await act(async () => { fireEvent.click(screen.getByText('Enregistrer la clé')); });
    await waitFor(() => expect(calls.some((c) => c.url === '/api/llm/key' && c.init?.method === 'PUT')).toBe(true));
    await waitFor(() => expect((screen.getByLabelText('Clé OpenRouter') as HTMLInputElement).value).toBe(''));
    expect(document.body.textContent).not.toContain('sk-or-v1-secret');
  });

  it('Réglages IA : un modèle par défaut est proposé et le message indique l’absence de clé', async () => {
    mockApi({
      '/api/settings': { values: { llm_enabled: true }, defaults: {} },
      '/api/llm/status': { configured: false, masked: '', source: 'none', available: false, default_model: 'minimax/minimax-m3.1-flash' },
      '/api/llm/stats': { today: { calls: 0, errors: 0, tokens: 0, cost_usd: 0 }, recent: [], by_model: [], last_error: null },
      '/api/llm/models': { default: 'minimax/minimax-m3.1-flash', items: [] },
      '/api/agents': { items: [], provenance: {}, pipeline: {} },
    });
    const { default: Settings } = await import('../pages/Settings');
    wrap(<Settings />);
    fireEvent.click(await screen.findByRole('tab', { name: 'Intelligence artificielle' }));
    expect(await screen.findByText('Aucune clé')).toBeTruthy();
    expect(screen.getByPlaceholderText('minimax/minimax-m3.1-flash')).toBeTruthy();
  });

  it('Ordres : une proposition en attente peut être approuvée ou refusée, et l’approbation envoie « yes »', async () => {
    const proposal = { id: 7, ts: '2026-10-29T10:00:00Z', portfolio_id: 1, symbol: 'EURUSD', status: 'pending', decision: 'auto', sizing: { qty: 1000 }, risk: { verdict: 'PASS' }, plan: {}, correlation_id: 'c' };
    const calls = mockApi({
      '/api/portfolios': { items: [{ id: 1, name: 'Principal' }] },
      '/api/proposals/7/decide': () => ({ status: 'executed', note: 'ok' }),
      '/api/proposals': { items: [proposal] },
      '/api/orders': { items: [] },
    });
    const { default: Orders } = await import('../pages/Orders');
    wrap(<Orders />);
    expect(await screen.findByText('Approuver')).toBeTruthy();
    expect(screen.getByText('Refuser')).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByText('Approuver')); });
    const d = calls.find((c) => c.url === '/api/proposals/7/decide');
    expect(d).toBeTruthy();
    expect(JSON.parse(String(d!.init!.body))).toEqual({ reply: 'yes' });
  });

  it('Ordres : état vide explicite quand il n’y a aucune proposition', async () => {
    mockApi({ '/api/portfolios': { items: [] }, '/api/proposals': { items: [] }, '/api/orders': { items: [] } });
    const { default: Orders } = await import('../pages/Orders');
    wrap(<Orders />);
    expect(await screen.findByText('Aucune proposition')).toBeTruthy();
  });

  it('Diagnostic : un composant non configuré est « À vérifier », pas « OK »', async () => {
    mockApi({
      '/api/diagnostics': {
        checks: [{ name: 'IA (OpenRouter)', ok: null, detail: 'aucune clé', level: 'warning' }, { name: 'Base de données', ok: true, detail: 'ok', level: 'ok' }],
        feed: { stream: { state: 'connected' }, instruments: 3, quotes: 3, live: 3, stale: 0, closed: 0, live_threshold_s: 30 },
        pipeline: { pipeline_enabled: true, auto_execute: true, kill_switch: false, tasks: 3, watchlist: [], running: {}, last_runs: {} },
        recent_errors: [], llm: { today: { calls: 0, errors: 0, cost_usd: 0 }, last_error: null, last_ok_at: null }, provenance: { repo: 'r', commit: 'abcdef0', license: 'MIT' }, agents: ['scanner'], server_time: '2026-10-29T10:00:00Z', ws_clients: 0,
      },
    });
    const { default: Diagnostics } = await import('../pages/Diagnostics');
    wrap(<Diagnostics />);
    expect(await screen.findByText('À vérifier')).toBeTruthy();
    expect(screen.getAllByText('OK').length).toBeGreaterThan(0);
  });

  it('AgentDetail : le Broker n’a pas d’exécution manuelle', async () => {
    mockApi({
      '/api/agents/broker': { id: 'broker', name: 'Agent Broker', mission: 'm', skill: 'trader-broker', skill_version: 'abcdef0', implementation: 'partial', limitations: ['Courtier = moteur papier'], dependencies: [], uses_llm: false, default_params: {}, param_help: {}, inputs: [], outputs: [], config: { enabled: true, params: {}, effective_params: {}, timeout_s: 20, retries: 0 }, run_counts: {}, last_run: null },
      '/api/llm/models': { default: 'x', items: [] },
    });
    const { default: AgentDetail } = await import('../pages/AgentDetail');
    render(<MemoryRouter initialEntries={['/agents/broker']}><ToastProvider><Routes_ /></ToastProvider></MemoryRouter>);
    expect(await screen.findByText('Partiellement implémenté')).toBeTruthy();
    fireEvent.click(screen.getByRole('tab', { name: 'Exécuter' }));
    expect(await screen.findByText('Exécution manuelle indisponible')).toBeTruthy();
    function Routes_() { return <RoutesWrap Comp={AgentDetail} />; }
  });
});

import { Route, Routes } from 'react-router-dom';
function RoutesWrap({ Comp }: { Comp: React.ComponentType }) {
  return <Routes><Route path="/agents/:id" element={<Comp />} /></Routes>;
}

describe('thème', () => {
  it('applique et persiste le mode, « système » suit la préférence du navigateur', () => {
    applyTheme('light');
    expect(document.documentElement.dataset.theme).toBe('light');
    applyTheme('dark');
    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(readMode()).toBe('system');
    localStorage.setItem('td.theme', 'light');
    expect(readMode()).toBe('light');
  });
});

describe('ordre manuel', () => {
  it('un ordre refusé par le moteur (HTTP 201 mais statut rejected) n’est jamais présenté comme envoyé', async () => {
    const calls = mockApi({
      '/api/portfolios': { items: [{ id: 1, name: 'P' }] },
      '/api/orders': () => ({ id: 9, status: 'rejected', reject_reason: 'marge insuffisante' }),
      '/api/instruments': { items: [] },
    });
    const { OrderModal } = await import('../components/OrderModal');
    const { MarketProvider } = await import('../lib/market');
    const onClose = vi.fn();
    render(<MemoryRouter><ToastProvider><MarketProvider><OrderModal symbol="EURUSD" onClose={onClose} /></MarketProvider></ToastProvider></MemoryRouter>);
    await waitFor(() => expect((screen.getByLabelText('Quantité') as HTMLInputElement)).toBeTruthy());
    fireEvent.change(screen.getByLabelText('Quantité'), { target: { value: '1000' } });
    const btn = screen.getByText('Envoyer l’ordre');
    await waitFor(() => expect((btn as HTMLButtonElement).disabled).toBe(false));
    await act(async () => { fireEvent.click(btn); });
    expect(await screen.findByText(/marge insuffisante/)).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
    expect(calls.some((c) => c.url === '/api/orders')).toBe(true);
  });
});
