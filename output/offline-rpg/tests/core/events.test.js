'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
// vm contexts have their own Array prototypes; loose deepEqual where drained arrays cross realms.
const deepEq = require('node:assert').deepEqual;
const fs = require('node:fs');
const path = require('node:path');
const { loadCore } = require('./helpers');

const RPG = loadCore(['namespace', 'events']);
const { create, NAMES } = RPG.core.events;

test('emit delivers to listeners in registration order with payload and type', () => {
  const bus = create();
  const seen = [];
  bus.on('hit', (p, t) => seen.push(['a', p, t]));
  bus.on('hit', (p, t) => seen.push(['b', p, t]));
  bus.emit('hit', { dmg: 5 });
  assert.deepEqual(seen, [['a', { dmg: 5 }, 'hit'], ['b', { dmg: 5 }, 'hit']]);
});

test('listeners only receive their own type', () => {
  const bus = create();
  let hits = 0;
  bus.on('hit', () => hits++);
  bus.emit('kill', {});
  assert.equal(hits, 0);
});

test('on returns an unsubscribe function and off removes the listener', () => {
  const bus = create();
  let n = 0;
  const fn = () => n++;
  const unsub = bus.on('kill', fn);
  bus.emit('kill');
  unsub();
  bus.emit('kill');
  bus.on('kill', fn);
  bus.off('kill', fn);
  bus.emit('kill');
  bus.off('unknown', fn); // no-op, must not throw
  assert.equal(n, 1);
});

test('registering the same listener twice delivers once', () => {
  const bus = create();
  let n = 0;
  const fn = () => n++;
  bus.on('save', fn);
  bus.on('save', fn);
  bus.emit('save');
  assert.equal(n, 1);
});

test('a listener that unsubscribes during emit does not skip the next one', () => {
  const bus = create();
  const order = [];
  const first = () => { order.push('first'); bus.off('load', first); };
  bus.on('load', first);
  bus.on('load', () => order.push('second'));
  bus.emit('load');
  assert.deepEqual(order, ['first', 'second']);
});

test('a throwing listener is counted, does not stop others, and does not reach the emitter', () => {
  const bus = create();
  const order = [];
  bus.on('levelUp', () => { throw new Error('boom'); });
  bus.on('levelUp', () => order.push('ok'));
  bus.on('levelUp', () => { throw new TypeError('again'); });
  assert.equal(bus.errors, 0);
  assert.doesNotThrow(() => bus.emit('levelUp', 3));
  assert.deepEqual(order, ['ok']);
  assert.equal(bus.errors, 2);
  bus.emit('levelUp', 4);
  assert.equal(bus.errors, 4);
});

test('drain returns queued events in emit order and clears the queue', () => {
  const bus = create();
  bus.emit('hit', 1);
  bus.emit('kill', 2);
  bus.emit('hit', 3);
  deepEq(bus.drain(), [
    { type: 'hit', payload: 1 },
    { type: 'kill', payload: 2 },
    { type: 'hit', payload: 3 }
  ]);
  deepEq(bus.drain(), []);
  bus.emit('pickup', 'gold');
  deepEq(bus.drain(), [{ type: 'pickup', payload: 'gold' }]);
});

test('drain includes events whose listeners threw, so the UI still sees them', () => {
  const bus = create();
  bus.on('hit', () => { throw new Error('x'); });
  bus.emit('hit', 'p');
  deepEq(bus.drain(), [{ type: 'hit', payload: 'p' }]);
});

test('each create() gives an independent bus', () => {
  const a = create(), b = create();
  let n = 0;
  a.on('hit', () => n++);
  b.emit('hit');
  assert.equal(n, 0);
  deepEq(a.drain(), []);
  deepEq(b.drain(), [{ type: 'hit', payload: undefined }]);
});

test('the shared RPG.core.events instance exposes the same API', () => {
  const shared = RPG.core.events;
  for (const key of ['on', 'off', 'emit', 'drain', 'create', 'NAMES']) assert.ok(key in shared, key);
  assert.equal(typeof shared.errors, 'number');
});

test('NAMES is frozen and maps every constant to its event string', () => {
  assert.ok(Object.isFrozen(NAMES));
  assert.equal(NAMES.HIT, 'hit');
  assert.equal(NAMES.LEVEL_UP, 'levelUp');
  assert.equal(NAMES.UI_SOUND, 'uiSound');
});

test('NAMES lists every event type named in docs/ARCHITECTURE.md', () => {
  const doc = fs.readFileSync(path.join(__dirname, '..', '..', 'docs', 'ARCHITECTURE.md'), 'utf8');
  const block = doc.match(/\/\/ event types:([^\n]*(?:\n\/\/ [^\n]*)*)/);
  assert.ok(block, 'event types block not found in ARCHITECTURE.md');
  const documented = [...block[1].matchAll(/'([A-Za-z]+)'/g)].map((m) => m[1]);
  assert.ok(documented.length > 0);
  const values = new Set(Object.values(NAMES));
  for (const name of documented) assert.ok(values.has(name), `NAMES is missing '${name}'`);
});
