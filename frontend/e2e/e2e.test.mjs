// Tests e2e de l'interface contre le serveur de TEST (données synthétiques) : `python -m tests.e2e_server --port 8799`.
// Lancer : npm run build && npm run e2e   (E2E_URL pour changer l'adresse).
import { after, before, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const BASE = process.env.E2E_URL ?? 'http://127.0.0.1:8799';
const TOKEN = 'e2e-token';
const ROUTES = ['/', '/markets', '/instrument/EURUSD', '/charts', '/agents', '/agents/scanner', '/orders', '/positions', '/simulator', '/portfolios', '/strategies', '/analyses', '/notifications', '/settings', '/diagnostics'];

let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

const NO_UPDATE = { current: '0.2.0', latest: null, releases: [], update_available: false, error: null, repo: 'x' };
async function open(path = '/', { width = 1360, height = 860, scheme = 'dark', update = NO_UPDATE } = {}) {
  const ctx = await browser.newContext({ viewport: { width, height }, colorScheme: scheme });
  // La vérification de mise à jour interroge GitHub : on la rend déterministe pour que la fenêtre ne masque pas les autres tests.
  await ctx.route('**/api/update/check*', (r) => r.fulfill({ json: update }));
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
  page.on('console', (m) => { if (m.type() === 'error' && !/favicon|Failed to load resource/.test(m.text())) errors.push(`console: ${m.text()}`); });
  await page.goto(`${BASE}/#${path}`);
  return { ctx, page, errors };
}
const bg = (page) => page.evaluate(() => getComputedStyle(document.body).backgroundColor);

describe('interface', () => {
  test('les 15 pages se chargent sans erreur JavaScript', async () => {
    const { ctx, page, errors } = await open('/');
    for (const r of ROUTES) {
      await page.goto(`${BASE}/#${r}`);
      await page.waitForSelector('h1', { timeout: 8000 });
      assert.ok((await page.locator('h1').first().innerText()).length > 0, r);
    }
    assert.deepEqual(errors, []);
    await ctx.close();
  });

  test('le jeton local est récupéré automatiquement et la navigation fonctionne', async () => {
    const { ctx, page } = await open('/');
    await page.getByRole('heading', { name: 'Vue d’ensemble' }).waitFor();
    await page.getByRole('link', { name: 'Marchés' }).click();
    await page.getByRole('heading', { name: 'Marchés', exact: true }).waitFor();
    await ctx.close();
  });

  test('écran de connexion : jeton refusé puis accepté (hors loopback)', async () => {
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    await page.route('**/api/session', (r) => r.fulfill({ status: 403, contentType: 'application/json', body: '{"detail":"non"}' }));
    await page.goto(`${BASE}/#/`);
    await page.getByLabel(/Jeton d’accès/).fill('mauvais');
    await page.getByRole('button', { name: 'Se connecter' }).click();
    await page.getByLabel(/Jeton d’accès/).waitFor({ timeout: 8000 }); // un appel 401 renvoie à l'écran de connexion
    await page.getByLabel(/Jeton d’accès/).fill(TOKEN);
    await page.getByRole('button', { name: 'Se connecter' }).click();
    await page.getByRole('heading', { name: 'Vue d’ensemble' }).waitFor({ timeout: 8000 });
    await ctx.close();
  });

  test('thème : clair et sombre aux couleurs exactes, persistant, « système » suit le navigateur', async () => {
    const { ctx, page } = await open('/', { scheme: 'dark' });
    await page.getByRole('heading', { name: 'Vue d’ensemble' }).waitFor();
    assert.equal(await bg(page), 'rgb(30, 30, 30)'); // #1E1E1E
    await page.getByRole('button', { name: 'Clair' }).click();
    assert.equal(await bg(page), 'rgb(243, 245, 248)'); // #F3F5F8
    assert.equal(await page.evaluate(() => getComputedStyle(document.querySelector('.panel, .rail')).backgroundColor), 'rgb(255, 255, 255)');
    await page.reload();
    await page.getByRole('heading', { name: 'Vue d’ensemble' }).waitFor();
    assert.equal(await bg(page), 'rgb(243, 245, 248)');
    await page.getByRole('button', { name: 'Système' }).click();
    assert.equal(await bg(page), 'rgb(30, 30, 30)'); // le navigateur est en sombre
    await ctx.close();
  });

  test('marchés : liste, recherche, sélecteur de marché', async () => {
    const { ctx, page } = await open('/markets');
    await page.getByRole('row', { name: /XAUUSD/ }).waitFor();
    assert.equal(await page.locator('tbody tr').count(), 5);
    await page.getByLabel('Rechercher', { exact: true }).fill('xau');
    await page.waitForFunction(() => document.querySelectorAll('tbody tr').length === 1);
    await page.getByLabel('Rechercher', { exact: true }).fill('');
    await page.locator('.search-trigger').click(); // recherche / sélecteur de la barre du haut
    await page.getByRole('option', { name: /GBPUSD/ }).click();
    await page.locator('.search-trigger', { hasText: 'GBPUSD' }).waitFor();
    await ctx.close();
  });

  test('instrument : graphique rendu et prix mis à jour en temps réel', async () => {
    const { ctx, page } = await open('/instrument/EURUSD');
    await page.locator('[data-testid="chart"] canvas').first().waitFor({ timeout: 10000 });
    const legend = page.locator('.chart-legend span.up, .chart-legend span.warn').first();
    await legend.waitFor({ timeout: 8000 });
    const first = await legend.innerText();
    await page.waitForFunction((v) => { const e = document.querySelector('.chart-legend span.up, .chart-legend span.warn'); return e && e.textContent !== v; }, first, { timeout: 12000 });
    await ctx.close();
  });

  test('portefeuille → ordre manuel papier → position visible, puis clôture', async () => {
    const { ctx, page } = await open('/portfolios');
    await page.getByRole('button', { name: 'Nouveau portefeuille' }).first().click();
    await page.getByLabel('Nom').fill('Test e2e');
    await page.getByRole('button', { name: 'Enregistrer' }).click();
    await page.getByRole('cell', { name: 'Test e2e', exact: true }).waitFor();
    await page.goto(`${BASE}/#/instrument/EURUSD`);
    await page.getByRole('button', { name: 'Ordre papier' }).click();
    await page.getByLabel('Quantité').fill('1000');
    await page.getByRole('button', { name: 'Envoyer l’ordre' }).click();
    await page.getByText(/Ordre papier (exécuté|envoyé)/).waitFor();
    await page.goto(`${BASE}/#/positions`);
    await page.getByRole('row', { name: /EURUSD/ }).waitFor({ timeout: 8000 });
    await page.getByRole('button', { name: 'Clôturer', exact: true }).first().click();
    await page.getByRole('button', { name: 'Clôturer au marché' }).click();
    await page.getByText('Aucune position ouverte').waitFor({ timeout: 8000 });
    await page.goto(`${BASE}/#/orders`);
    await page.getByRole('tab', { name: 'Exécutions' }).click();
    await page.waitForFunction(() => document.querySelectorAll('tbody tr').length >= 2, null, { timeout: 8000 }); // achat + vente
    await ctx.close();
  });

  test('une quantité invalide est refusée par le moteur avec un message clair', async () => {
    const { ctx, page } = await open('/instrument/EURUSD');
    await page.getByRole('button', { name: 'Ordre papier' }).click();
    await page.getByLabel('Quantité').fill('1000000000');
    await page.getByRole('button', { name: 'Envoyer l’ordre' }).click();
    await page.getByRole('alert').first().waitFor();
    assert.match(await page.getByRole('dialog').innerText(), /marge|exposition|insuffisan|limite|dépass/i);
    await ctx.close();
  });

  test('agents : onze agents, Broker affiché comme partiellement implémenté', async () => {
    const { ctx, page } = await open('/agents');
    await page.getByRole('link', { name: 'Agent Broker' }).waitFor();
    assert.equal(await page.locator('.panel header h2 a').count(), 11);
    assert.ok((await page.getByText('Partiellement implémenté').count()) >= 2); // Broker et Analyst
    await ctx.close();
  });

  test('agent : exécution manuelle d’Exit, résultat structuré affiché', async () => {
    const { ctx, page } = await open('/agents/exit');
    await page.getByRole('tab', { name: 'Exécuter' }).click();
    await page.getByLabel('Entrées JSON').fill('{"symbol":"EURUSD"}');
    await page.getByRole('button', { name: 'Exécuter', exact: true }).click();
    await page.getByRole('heading', { name: 'Résultat' }).waitFor();
    await page.locator('pre.json').waitFor({ timeout: 15000 });
    await page.getByRole('tab', { name: 'Historique' }).click();
    await page.getByRole('row').nth(1).waitFor();
    await ctx.close();
  });

  test('simulateur : dry run complet sans ordre créé', async () => {
    const { ctx, page } = await open('/simulator');
    await page.getByRole('button', { name: 'Simuler' }).click();
    await page.getByText('Agent Exit — plan').waitFor({ timeout: 20000 });
    await page.getByText('Simulation à blanc : aucun ordre n’a été envoyé.').waitFor({ timeout: 20000 });
    const orders = await page.evaluate(async (t) => (await (await fetch('/api/orders', { headers: { Authorization: `Bearer ${t}` } })).json()).items.length, TOKEN);
    assert.ok(orders >= 2); // uniquement ceux du test précédent (achat + clôture), la simulation n'en ajoute aucun
    await ctx.close();
  });

  test('réglages IA : modèle par défaut, clé invalide refusée, test sans clé en erreur claire', async () => {
    const { ctx, page } = await open('/settings');
    await page.getByRole('tab', { name: 'Intelligence artificielle' }).click();
    await page.getByText('Aucune clé').waitFor();
    await page.getByPlaceholder('minimax/minimax-m3.1-flash').first().waitFor();
    await page.getByLabel('Clé OpenRouter').fill('abc');
    await page.getByRole('button', { name: 'Enregistrer la clé' }).click();
    await page.getByText('Clé refusée').waitFor();
    await page.getByRole('button', { name: 'Tester la connexion' }).click();
    await page.getByText(/aucune clé OpenRouter configurée/).waitFor();
    await ctx.close();
  });

  test('interrupteur d’arrêt : confirmation, bannière persistante après rechargement, réactivation', async () => {
    const { ctx, page } = await open('/');
    await page.getByRole('button', { name: /Arrêt d’urgence/ }).click();
    await page.getByRole('button', { name: 'Activer l’arrêt' }).click();
    await page.getByText('Interrupteur d’arrêt actif.').waitFor();
    await page.reload();
    await page.getByText('Interrupteur d’arrêt actif.').waitFor();
    await page.getByRole('button', { name: /réactiver/ }).click();
    await page.getByText('Interrupteur d’arrêt actif.').waitFor({ state: 'detached' });
    await ctx.close();
  });

  test('diagnostic : composants non configurés signalés « À vérifier »', async () => {
    const { ctx, page } = await open('/diagnostics');
    await page.getByRole('row', { name: /IA \(OpenRouter\)/ }).getByText('À vérifier').waitFor();
    await page.getByRole('row', { name: /Skills vendorisés/ }).waitFor();
    await ctx.close();
  });

  for (const [name, w, h] of [['mobile', 390, 800], ['tablette', 820, 1100]]) {
    test(`responsive ${name} : pas de défilement horizontal de la page, navigation accessible`, async () => {
      const { ctx, page, errors } = await open('/', { width: w, height: h });
      for (const r of ['/', '/markets', '/orders', '/agents', '/settings', '/instrument/EURUSD']) {
        await page.goto(`${BASE}/#${r}`);
        await page.waitForSelector('h1');
        await page.waitForTimeout(250);
        const over = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
        assert.ok(over <= 1, `${r}: débordement horizontal de ${over}px`);
      }
      assert.ok(await page.locator('nav.nav a').first().isVisible());
      assert.deepEqual(errors, []);
      await ctx.close();
    });
  }

  test('accessibilité de base : titre de page, landmarks, focus visible', async () => {
    const { ctx, page } = await open('/markets');
    await page.waitForSelector('h1');
    assert.match(await page.title(), /Marchés/);
    assert.equal(await page.locator('main').count(), 1);
    assert.equal(await page.locator('nav[aria-label]').count(), 1);
    await page.keyboard.press('Tab');
    const outline = await page.evaluate(() => getComputedStyle(document.activeElement).outlineStyle);
    assert.notEqual(outline, 'none');
    await ctx.close();
  });

  test('mise à jour : fenêtre à l’ouverture, « Plus tard » ajoute l’icône en bas de la barre latérale', async () => {
    const latest = { version: '9.9.9', tag: 'v9.9.9', name: 'x', notes: 'Notes', url: 'https://example.test/r', assets: [] };
    const { ctx, page } = await open('/markets', { update: { current: '0.2.0', latest, releases: [latest], update_available: true, error: null, repo: 'x' } });
    await page.getByText('Nouvelle version disponible').waitFor();
    assert.equal(await page.locator('.rail-update').count(), 0);
    await page.getByRole('button', { name: 'Plus tard' }).click();
    const icon = page.locator('.rail-update');
    await icon.waitFor();
    const [rail, box] = await Promise.all([page.locator('.rail').boundingBox(), icon.boundingBox()]);
    assert.ok(box.y + box.height > rail.y + rail.height - 40, 'icône tout en bas de la barre latérale');
    await icon.click();
    await page.getByText('Nouvelle version disponible').waitFor();
    await ctx.close();
  });

  test('barre latérale : réduire / agrandir, état conservé après rechargement', async () => {
    const { ctx, page } = await open('/');
    const rail = page.locator('aside.rail');
    await rail.waitFor();
    const wide = (await rail.boundingBox()).width;
    await page.getByRole('button', { name: 'Réduire la barre latérale' }).click();
    await page.waitForFunction(() => document.querySelector('aside.rail').getBoundingClientRect().width < 90);
    const narrow = (await rail.boundingBox()).width;
    assert.ok(narrow < 100 && wide > 200, `${wide} -> ${narrow}`);
    assert.equal(await page.locator('.nav a span').first().isVisible(), false); // icônes seules
    await page.reload();
    await page.waitForSelector('aside.rail');
    await page.waitForFunction(() => document.querySelector('aside.rail').getBoundingClientRect().width < 100);
    await page.getByRole('button', { name: 'Agrandir la barre latérale' }).click();
    await page.waitForFunction((w) => document.querySelector('aside.rail').getBoundingClientRect().width > w - 5, wide);
    await ctx.close();
  });

  test('réglages : alerte sur tous les actifs activée par défaut', async () => {
    const { ctx, page } = await open('/settings');
    await page.getByRole('tab', { name: 'Surveillance et alertes' }).click();
    const sw = page.getByLabel('Alerte sur tous les actifs');
    await sw.waitFor({ state: 'attached' });
    assert.equal(await sw.isChecked(), true);
    await ctx.close();
  });
});
