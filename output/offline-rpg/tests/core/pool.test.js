'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadCore } = require('./helpers');

const RPG = loadCore(['namespace', 'util', 'pool']);
const { Pool } = RPG.core;

/** Factory that counts how many objects it has built, so tests can tell reuse from allocation. */
function counting() {
  let built = 0;
  const factory = () => ({ id: ++built, hp: 10, dead: false });
  return { factory, built: () => built };
}

test('acquire on an empty pool builds a new object and marks it active', () => {
  const c = counting();
  const pool = Pool.create(c.factory);
  const a = pool.acquire();
  assert.equal(c.built(), 1);
  assert.equal(pool.active.length, 1);
  assert.equal(pool.active[0], a);
});

test('release returns the object to the free list and the next acquire reuses that same object', () => {
  const c = counting();
  const pool = Pool.create(c.factory);
  const a = pool.acquire();
  assert.equal(pool.release(a), true);
  assert.equal(pool.active.length, 0);
  assert.equal(pool.freeCount(), 1);
  const b = pool.acquire();
  assert.equal(b, a, 'reused, not reallocated');
  assert.equal(c.built(), 1);
});

test('prealloc builds the free list up front so the first acquires do not call the factory', () => {
  const c = counting();
  const pool = Pool.create(c.factory, null, 3);
  assert.equal(c.built(), 3);
  assert.equal(pool.freeCount(), 3);
  pool.acquire();
  pool.acquire();
  pool.acquire();
  assert.equal(c.built(), 3);
  assert.equal(pool.freeCount(), 0);
});

test('exhaustion: with the free list empty, acquire keeps working by building more (pool is unbounded)', () => {
  const c = counting();
  const pool = Pool.create(c.factory, null, 1);
  pool.acquire();
  const second = pool.acquire();
  assert.equal(c.built(), 2);
  assert.equal(pool.active.length, 2);
  assert.ok(second && second.id === 2);
});

test('reset runs on release (not on acquire) so a reused object comes back clean', () => {
  const c = counting();
  const resets = [];
  const pool = Pool.create(c.factory, (o) => { resets.push(o.id); o.hp = 10; o.dead = false; });
  const a = pool.acquire();
  a.hp = 0;
  a.dead = true;
  assert.deepEqual(resets, []);
  pool.release(a);
  assert.deepEqual(resets, [a.id]);
  const b = pool.acquire();
  assert.equal(b.hp, 10);
  assert.equal(b.dead, false);
  assert.deepEqual(resets, [a.id], 'acquire does not reset');
});

test('double release is a no-op that returns false and does not duplicate the object in the free list', () => {
  const c = counting();
  const pool = Pool.create(c.factory);
  const a = pool.acquire();
  assert.equal(pool.release(a), true);
  assert.equal(pool.release(a), false);
  assert.equal(pool.freeCount(), 1);
  // Two acquires must still yield two different objects.
  const x = pool.acquire();
  const y = pool.acquire();
  assert.notEqual(x, y);
});

test('releasing an object the pool never handed out returns false and changes nothing', () => {
  const c = counting();
  const pool = Pool.create(c.factory, null, 1);
  const stranger = { id: 99 };
  assert.equal(pool.release(stranger), false);
  assert.equal(pool.freeCount(), 1);
  assert.equal(pool.active.length, 0);
});

test('release swap-removes from the active list without losing the other active objects', () => {
  const c = counting();
  const pool = Pool.create(c.factory);
  const a = pool.acquire();
  const b = pool.acquire();
  const d = pool.acquire();
  pool.release(a);
  assert.equal(pool.active.length, 2);
  assert.ok(pool.active.includes(b));
  assert.ok(pool.active.includes(d));
  assert.ok(!pool.active.includes(a));
});
