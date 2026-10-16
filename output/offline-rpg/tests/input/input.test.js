'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { loadInput, plain } = require('./load.js');

const CAMERA = { x: 100, y: 50, zoom: 2 };
const VIEW_W = 800;
const VIEW_H = 600;

// Event.target is a getter on the host Event, so fake properties are defined as own properties to shadow it.
function makeEvent(type, props) {
  const e = new Event(type);
  for (const [name, value] of Object.entries(props)) {
    Object.defineProperty(e, name, { value, configurable: true, writable: true });
  }
  return e;
}

// Fresh game instance with the window and canvas wired to host EventTargets.
function setup() {
  const input = loadInput();
  const game = input.create();
  const win = new EventTarget();
  const canvas = new EventTarget();
  canvas.getBoundingClientRect = () => ({ left: 0, top: 0 });
  game.attach(win, canvas);
  const key = (type, code, extra = {}) => {
    const e = makeEvent(type, { code, repeat: false, target: null, ...extra });
    win.dispatchEvent(e);
    return e;
  };
  const mouse = (type, button, x = 0, y = 0) => {
    const e = makeEvent(type, { button, clientX: x, clientY: y });
    (type === 'mouseup' ? win : canvas).dispatchEvent(e);
  };
  const frame = () => plain(game.buildFrame(CAMERA, VIEW_W, VIEW_H));
  return { input, game, win, canvas, key, mouse, frame };
}

test('diagonal movement is normalised and not faster than cardinal', () => {
  const { key, frame } = setup();
  key('keydown', 'KeyW');
  assert.deepEqual(frame().move, { x: 0, y: -1 });

  key('keyup', 'KeyW');
  key('keydown', 'KeyD');
  key('keydown', 'KeyW');
  const diag = frame().move;
  const len = Math.hypot(diag.x, diag.y);
  assert.ok(Math.abs(len - 1) < 1e-9, `diagonal length ${len} should be 1`);
  assert.ok(Math.abs(diag.x - Math.SQRT1_2) < 1e-9);
  assert.ok(Math.abs(diag.y + Math.SQRT1_2) < 1e-9);
});

test('opposite keys cancel and no keys gives a zero vector', () => {
  const { key, frame } = setup();
  assert.deepEqual(frame().move, { x: 0, y: 0 });
  key('keydown', 'KeyA');
  key('keydown', 'ArrowRight');
  assert.deepEqual(frame().move, { x: 0, y: 0 });
});

test('rebinding a key changes the intent mapping and the old key stops working', () => {
  const { game, key, frame } = setup();
  game.setBinding('moveUp', 'KeyT');
  key('keydown', 'KeyT');
  assert.deepEqual(frame().move, { x: 0, y: -1 });
  key('keyup', 'KeyT');
  key('keydown', 'KeyW');
  assert.deepEqual(frame().move, { x: 0, y: 0 }, 'KeyW was unbound from moveUp');
});

test('rebinding a key removes it from the action it previously did', () => {
  const { game, key, frame } = setup();
  game.setBinding('dash', 'KeyI');
  key('keydown', 'KeyI');
  const f = frame();
  assert.equal(f.dash, true);
  assert.equal(f.toggles.inventory, false, 'KeyI no longer toggles inventory');
});

test('an unknown key is ignored', () => {
  const { key, frame } = setup();
  key('keydown', 'KeyZ');
  const f = frame();
  assert.deepEqual(f.move, { x: 0, y: 0 });
  assert.equal(f.attack, false);
  assert.equal(f.dash, false);
  assert.deepEqual(f.skills, [false, false, false, false, false, false, false]);
  assert.equal(f.toggles.pause, false);
});

test('setBinding rejects an unknown action or an empty code', () => {
  const { game } = setup();
  assert.throws(() => game.setBinding('jump', 'KeyJ'), /unknown input action jump/);
  assert.throws(() => game.setBinding('dash', ''), /invalid key code/);
});

test('resetBindings restores the defaults', () => {
  const { game, key, frame } = setup();
  game.setBinding('moveUp', 'KeyT');
  game.resetBindings();
  key('keydown', 'KeyW');
  assert.deepEqual(frame().move, { x: 0, y: -1 });
});

test('a rebound mouse button drives the attack action', () => {
  const { game, mouse, frame } = setup();
  game.setBinding('attack', 'Mouse2');
  mouse('mousedown', 0, 10, 10);
  assert.equal(frame().attack, false, 'left button no longer attacks');
  mouse('mousedown', 2, 10, 10);
  assert.equal(frame().attack, true);
});

test('window blur clears held keys and mouse buttons', () => {
  const { win, key, mouse, frame } = setup();
  key('keydown', 'KeyW');
  key('keydown', 'KeyD');
  mouse('mousedown', 0, 10, 10);
  let f = frame();
  assert.notDeepEqual(f.move, { x: 0, y: 0 });
  assert.equal(f.attack, true);

  win.dispatchEvent(new Event('blur'));
  f = frame();
  assert.deepEqual(f.move, { x: 0, y: 0 });
  assert.equal(f.attack, false);
});

test('a press is delivered once and auto-repeat does not retrigger a skill', () => {
  const { key, frame } = setup();
  key('keydown', 'Digit1');
  assert.equal(frame().skills[0], true);
  assert.equal(frame().skills[0], false, 'edge is consumed by buildFrame');

  key('keydown', 'Digit1', { repeat: true });
  assert.equal(frame().skills[0], false, 'auto-repeat while held creates no edge');
});

test('a tap between two frames is not lost', () => {
  const { key, frame } = setup();
  key('keydown', 'Space');
  key('keyup', 'Space');
  assert.equal(frame().dash, true);
  assert.equal(frame().dash, false);
});

test('skill slots map to skills[0..6] for keys 1-5, Q and E', () => {
  const codes = ['Digit1', 'Digit2', 'Digit3', 'Digit4', 'Digit5', 'KeyQ', 'KeyE'];
  codes.forEach((code, i) => {
    const { key, frame } = setup();
    key('keydown', code);
    const skills = frame().skills;
    assert.equal(skills.length, 7);
    assert.equal(skills[i], true, `${code} -> slot ${i}`);
    assert.equal(skills.filter(Boolean).length, 1);
  });
});

test('toggle keys map to named toggles, with Escape and P both pausing', () => {
  const cases = [
    ['KeyI', 'inventory'],
    ['KeyC', 'character'],
    ['KeyK', 'skills'],
    ['KeyO', 'settings'],
    ['Escape', 'pause'],
    ['KeyP', 'pause'],
    ['KeyM', 'saveMenu'],
    ['F5', 'quickSave'],
    ['F9', 'quickLoad'],
    ['F3', 'debug']
  ];
  for (const [code, toggle] of cases) {
    const { key, frame } = setup();
    key('keydown', code);
    const toggles = frame().toggles;
    assert.equal(toggles[toggle], true, `${code} -> ${toggle}`);
    assert.equal(Object.values(toggles).filter(Boolean).length, 1);
  }
});

test('aim is the camera centre with no pointer, and world coordinates once the pointer is seen', () => {
  const { mouse, frame } = setup();
  assert.deepEqual(frame().aim, { x: CAMERA.x, y: CAMERA.y });

  // Canvas centre maps to the camera centre; one screen px at zoom 2 is half a world unit.
  mouse('mousemove', 0, VIEW_W / 2, VIEW_H / 2);
  assert.deepEqual(frame().aim, { x: CAMERA.x, y: CAMERA.y });

  mouse('mousemove', 0, VIEW_W / 2 + 20, VIEW_H / 2 - 40);
  assert.deepEqual(frame().aim, { x: CAMERA.x + 10, y: CAMERA.y - 20 });
});

test('typing into a form control does not drive the character', () => {
  const { win, key, frame } = setup();
  win.dispatchEvent(makeEvent('keydown', { code: 'KeyW', repeat: false, target: { tagName: 'INPUT' } }));
  assert.deepEqual(frame().move, { x: 0, y: 0 });
  key('keydown', 'KeyW');
  assert.deepEqual(frame().move, { x: 0, y: -1 });
});

test('detach removes listeners', () => {
  const { game, key, frame } = setup();
  game.detach();
  key('keydown', 'KeyW');
  assert.deepEqual(frame().move, { x: 0, y: 0 });
});

test('getBindings returns a copy that cannot change the live map', () => {
  const { game, key, frame } = setup();
  const copy = plain(game.getBindings());
  copy.moveUp.length = 0;
  key('keydown', 'KeyW');
  assert.deepEqual(frame().move, { x: 0, y: -1 });
});
