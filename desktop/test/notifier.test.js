'use strict';
const test = require('node:test');
const assert = require('node:assert');
const { createNotifier, clamp } = require('../src/notifier');

class FakeNotification {
  static isSupported() { return true; }
  constructor(opts) { this.opts = opts; this.handlers = {}; FakeNotification.last = this; }
  on(e, h) { this.handlers[e] = h; }
  show() { this.shown = true; }
}

test('affiche une notification nettoyée et tronquée', () => {
  const n = createNotifier({ Notification: FakeNotification });
  assert.equal(n.show({ title: 'A'.repeat(500), body: 'ligne\n\x00deux', silent: true }), true);
  assert.equal(FakeNotification.last.opts.title.length, 120);
  assert.equal(FakeNotification.last.opts.body, 'ligne deux');
  assert.equal(FakeNotification.last.opts.silent, true);
  assert.ok(FakeNotification.last.shown);
});

test('titre par défaut et charge utile invalide', () => {
  const n = createNotifier({ Notification: FakeNotification });
  assert.equal(n.show(null), true);
  assert.equal(FakeNotification.last.opts.title, 'Monkora');
});

test('le clic transmet la route', () => {
  const routes = [];
  const n = createNotifier({ Notification: FakeNotification, onClick: (r) => routes.push(r) });
  n.show({ title: 't', body: 'b', route: '/notifications' });
  FakeNotification.last.handlers.click();
  assert.deepEqual(routes, ['/notifications']);
});

test('sans support, ne fait rien', () => {
  const n = createNotifier({ Notification: { isSupported: () => false } });
  assert.equal(n.show({ title: 'x' }), false);
  assert.equal(clamp(undefined, 5), '');
});
