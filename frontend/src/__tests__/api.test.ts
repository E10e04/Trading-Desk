import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError, getToken, setToken, bootstrapToken } from '../lib/api';

const respond = (status: number, body: unknown) => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }));

describe('api', () => {
  beforeEach(() => { localStorage.clear(); setToken(null); });
  afterEach(() => vi.restoreAllMocks());

  it('envoie le jeton en Authorization', async () => {
    setToken('tok');
    const f = vi.spyOn(globalThis, 'fetch').mockImplementation(() => respond(200, { ok: 1 }));
    await api('/api/x');
    expect((f.mock.calls[0][1] as RequestInit).headers).toMatchObject({ Authorization: 'Bearer tok' });
  });
  it('POST quand un corps est fourni', async () => {
    const f = vi.spyOn(globalThis, 'fetch').mockImplementation(() => respond(200, {}));
    await api('/api/x', { body: { a: 1 } });
    expect((f.mock.calls[0][1] as RequestInit).method).toBe('POST');
  });
  it('un 401 efface le jeton et signale la session expirée', async () => {
    setToken('mauvais');
    vi.spyOn(globalThis, 'fetch').mockImplementation(() => respond(401, { detail: 'x' }));
    await expect(api('/api/x')).rejects.toBeInstanceOf(ApiError);
    expect(getToken()).toBeNull();
  });
  it('extrait les raisons détaillées d’un refus du moteur', async () => {
    setToken('t');
    vi.spyOn(globalThis, 'fetch').mockImplementation(() => respond(422, { detail: { reasons: ['marge insuffisante', 'cotation périmée'] } }));
    await expect(api('/api/orders', { body: {} })).rejects.toThrow('marge insuffisante; cotation périmée');
  });
  it('serveur injoignable : message clair', async () => {
    setToken('t');
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('failed'));
    await expect(api('/api/x')).rejects.toThrow(/injoignable/);
  });
  it('bootstrapToken récupère le jeton local', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(() => respond(200, { token: 'local' }));
    expect(await bootstrapToken()).toBe(true);
    expect(getToken()).toBe('local');
  });
  it('bootstrapToken échoue proprement hors local (403)', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(() => respond(403, { detail: 'non' }));
    expect(await bootstrapToken()).toBe(false);
    expect(getToken()).toBeNull();
  });
});
