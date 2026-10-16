'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG, UI_FILES } = require('./load.js');

const RPG = loadRPG(UI_FILES);
const screens = RPG.ui.screens;

function playing() {
  const m = screens.create();
  m.open('playing');
  return m;
}

test('key bindings from ARCHITECTURE §9 open and close the matching screens', () => {
  const m = playing();
  assert.equal(m.handleKey('KeyI'), true);
  assert.equal(m.top(), 'inventory');
  assert.equal(m.handleKey('KeyI'), true);
  assert.equal(m.top(), 'playing');

  m.handleKey('KeyC');
  assert.equal(m.top(), 'character');
  m.handleKey('KeyC');
  assert.equal(m.top(), 'playing');

  m.handleKey('KeyK');
  assert.equal(m.top(), 'skills');
  m.handleKey('KeyK');
  assert.equal(m.top(), 'playing');

  m.handleKey('KeyO');
  assert.equal(m.top(), 'settings');
  m.handleKey('KeyO');
  assert.equal(m.top(), 'playing');

  m.handleKey('KeyM');
  assert.equal(m.top(), 'saveload');
  m.handleKey('KeyM');
  assert.equal(m.top(), 'playing');
});

test('S is move-down, not the skills screen', () => {
  const m = playing();
  assert.equal(m.handleKey('KeyS'), false);
  assert.equal(m.top(), 'playing');
});

test('Esc opens pause from playing, then closes the top screen first', () => {
  const m = playing();
  m.handleKey('KeyI');
  assert.deepEqual([...m.stack()], ['playing', 'inventory']);

  assert.equal(m.handleKey('Escape'), true);
  assert.deepEqual([...m.stack()], ['playing']);

  assert.equal(m.handleKey('Escape'), true);
  assert.equal(m.top(), 'pause');
  assert.equal(m.handleKey('KeyP'), true);
  assert.equal(m.top(), 'playing');
});

test('Esc on the main menu and unbound keys do nothing', () => {
  const m = screens.create();
  assert.equal(m.top(), 'mainmenu');
  assert.equal(m.handleKey('Escape'), false);
  assert.equal(m.handleKey('KeyI'), false);
  assert.equal(m.top(), 'mainmenu');

  const p = playing();
  assert.equal(p.handleKey('KeyZ'), false);
  assert.equal(p.top(), 'playing');
});

test('modal screens report the sim as paused, base screens do not', () => {
  const m = screens.create();
  assert.equal(m.paused(), false, 'main menu is not modal');
  m.open('playing');
  assert.equal(m.paused(), false, 'playing is not modal');
  m.open('inventory');
  assert.equal(m.paused(), true);
  m.close();
  assert.equal(m.paused(), false);
  m.open('pause');
  assert.equal(m.paused(), true);
});

test('opening the same modal twice does not stack duplicates', () => {
  const m = playing();
  m.open('inventory');
  m.open('inventory');
  assert.deepEqual([...m.stack()], ['playing', 'inventory']);
});

test('close(id) removes a modal below the top, and never removes a base screen', () => {
  const m = playing();
  m.open('inventory');
  m.open('character');
  assert.equal(m.close('inventory'), true);
  assert.deepEqual([...m.stack()], ['playing', 'character']);
  assert.equal(m.close('playing'), false);
  assert.equal(m.close('mainmenu'), false);
  assert.deepEqual([...m.stack()], ['playing', 'character']);
});

test('the stack never underflows', () => {
  const m = screens.create();
  assert.equal(m.close(), false, 'main menu cannot be popped');
  assert.equal(m.close(), false);
  assert.deepEqual([...m.stack()], ['mainmenu']);

  m.open('playing');
  assert.equal(m.close(), false, 'playing cannot be popped');
  assert.equal(m.top(), 'playing');

  m.open('pause');
  assert.equal(m.close(), true);
  assert.equal(m.close(), false);
  assert.deepEqual([...m.stack()], ['playing']);
});

test('base screens replace the stack', () => {
  const m = playing();
  m.open('inventory');
  m.open('mainmenu');
  assert.deepEqual([...m.stack()], ['mainmenu']);
});

test('unknown screen ids throw a clear error', () => {
  const m = screens.create();
  assert.throws(() => m.open('shop'), /unknown screen id: shop/);
  assert.throws(() => m.close('shop'), /unknown screen id: shop/);
  assert.throws(() => m.toggle('shop'), /unknown screen id: shop/);
  assert.throws(() => m.register({ id: 'shop' }), /unknown screen id: shop/);
  assert.deepEqual([...m.stack()], ['mainmenu']);
});

test('the registry lets later screens plug in with update, draw and onKey', () => {
  const m = playing();
  const calls = [];
  m.register({
    id: 'inventory',
    update: (dt) => calls.push(['update', dt]),
    draw: (ctx) => calls.push(['draw', ctx.name]),
    onKey: (code, action) => {
      calls.push(['key', code, action]);
      return code === 'KeyQ';
    }
  });

  m.open('inventory');
  m.update(16);
  m.draw({ name: 'ctx' });
  assert.deepEqual(calls, [
    ['update', 16],
    ['draw', 'ctx']
  ]);
  // Top screen gets first refusal; returning true consumes the key so the global handling is skipped.
  assert.equal(m.handleKey('KeyQ'), true);
  assert.equal(m.top(), 'inventory');
  // Returning false lets the global binding run: KeyI toggles the inventory closed.
  assert.equal(m.handleKey('KeyI'), true);
  assert.equal(m.top(), 'playing');
  assert.deepEqual(calls.at(-1), ['key', 'KeyI', 'toggleInventory']);
});

test('draw paints every stacked screen bottom to top, update runs only for the top', () => {
  const m = playing();
  const order = [];
  m.register({ id: 'playing', draw: () => order.push('playing'), update: () => order.push('update-playing') });
  m.register({ id: 'pause', draw: () => order.push('pause'), update: () => order.push('update-pause') });
  m.open('pause');
  m.update(0);
  m.draw({});
  assert.deepEqual(order, ['update-pause', 'playing', 'pause']);
});

test('register rejects non-function handlers', () => {
  const m = screens.create();
  assert.throws(() => m.register({ id: 'pause', draw: 'nope' }), /draw must be a function/);
  assert.throws(() => m.register(null), /must be an object/);
});

test('toggle opens a screen that is not on top and closes one that is', () => {
  const m = playing();
  assert.equal(m.toggle('settings'), true);
  assert.equal(m.top(), 'settings');
  assert.equal(m.toggle('settings'), true);
  assert.equal(m.top(), 'playing');
});

test('injected bindings replace the keymap defaults', () => {
  const m = screens.create({ bindings: { toggleInventory: ['KeyU'] } });
  m.open('playing');
  assert.equal(m.handleKey('KeyI'), false);
  assert.equal(m.handleKey('KeyU'), true);
  assert.equal(m.top(), 'inventory');
});
