/// <reference types="vite/client" />
// L'application de bureau embarque Electron 22 (Chromium 108) : color-mix() (Chromium 111) y est inconnu, et la bibliothèque de
// graphiques lève une erreur sur une couleur qu'elle ne sait pas lire — la courbe d'equity restait vide. Aucun style ne doit l'utiliser.
const sheets = import.meta.glob('../styles/*.css', { query: '?raw', import: 'default', eager: true }) as Record<string, string>;

test('les feuilles de style sont bien chargées', () => {
  expect(Object.keys(sheets).length).toBeGreaterThanOrEqual(3);
});

test.each(Object.entries(sheets))('%s n’utilise pas de fonction de couleur trop récente', (_name, raw) => {
  const css = raw.replace(/\/\*[\s\S]*?\*\//g, '');
  expect(css).not.toMatch(/color-mix\(|light-dark\(|oklch\(|oklab\(/);
});
