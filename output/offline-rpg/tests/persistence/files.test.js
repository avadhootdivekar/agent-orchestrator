'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const { sampleState } = require('./helpers.js');

// Load files.js into the same vm context as the other persistence files.
function loadWithFiles() {
  const ctx = vm.createContext({ RPG: {} });
  ctx.globalThis = ctx;
  const dir = path.join(__dirname, '..', '..', 'src', 'persistence');
  for (const f of ['migrate.js', 'save.js', 'storage.js', 'exportimport.js', 'files.js']) {
    vm.runInContext(fs.readFileSync(path.join(dir, f), 'utf8'), ctx, { filename: f });
  }
  return ctx.RPG.persistence;
}

function fakeDom() {
  const log = { blobs: [], revoked: [], clicked: [], appended: 0, removed: 0 };
  const a = { style: {}, click() { log.clicked.push({ href: a.href, download: a.download }); } };
  return {
    log,
    env: {
      document: { createElement: (t) => { assert.equal(t, 'a'); return a; },
        body: { appendChild() { log.appended++; }, removeChild() { log.removed++; } } },
      URL: { createObjectURL: (b) => { log.blobs.push(b); return 'blob:fake/1'; }, revokeObjectURL: (u) => log.revoked.push(u) },
      Blob: class { constructor(parts, opts) { this.parts = parts; this.type = opts && opts.type; } },
      setTimeout: (fn) => fn()
    }
  };
}

function fakeReader(content, mode) {
  return class {
    readAsText() {
      if (mode === 'error') { this.error = new Error('boom'); return this.onerror(); }
      if (mode === 'abort') return this.onabort();
      this.result = content;
      this.onload();
    }
  };
}

function read(P, file, content, mode, opts) {
  let calls = 0, out;
  P.files.readSaveFile(file, (r) => { calls++; out = r; }, { FileReader: fakeReader(content, mode) }, opts);
  assert.equal(calls, 1, 'callback called exactly once');
  return out;
}

const JSON_FILE = { name: 'a.json', type: 'application/json', size: 10 };

test('downloadSave builds a JSON blob and clicks an anchor named by exportFileName', () => {
  const P = loadWithFiles(), s = sampleState(), d = fakeDom();
  const r = P.files.downloadSave(s, d.env);
  assert.equal(r.ok, true);
  assert.equal(r.name, 'game-save-7-2026-10-16.json');
  assert.equal(r.name, P.exportFileName(s));
  assert.deepEqual(d.log.clicked, [{ href: 'blob:fake/1', download: r.name }]);
  assert.equal(d.log.blobs[0].type, 'application/json');
  assert.match(d.log.blobs[0].parts[0], /^\{\n {2}"/, 'pretty-printed');
  assert.equal(d.log.appended, 1); assert.equal(d.log.removed, 1);
  assert.deepEqual(d.log.revoked, ['blob:fake/1']);
});

test('downloadSave returns errors instead of throwing', () => {
  const P = loadWithFiles(), d = fakeDom();
  assert.equal(P.files.downloadSave({ nope: 1 }, d.env).ok, false);
  assert.equal(d.log.clicked.length, 0);
  const r = P.files.downloadSave(sampleState(), {});
  assert.equal(r.ok, false); assert.equal(r.code, 'unsupported');
  d.env.document.createElement = () => { throw new Error('dom gone'); };
  const r2 = P.files.downloadSave(sampleState(), d.env);
  assert.equal(r2.ok, false); assert.deepEqual(d.log.revoked, ['blob:fake/1']);
});

test('export -> import round trip through exportimport', () => {
  const P = loadWithFiles(), s = sampleState(), d = fakeDom();
  assert.equal(P.files.downloadSave(s, d.env).ok, true);
  const text = d.log.blobs[0].parts[0];
  const r = read(P, { name: 'game-save-7-2026-10-16.json', type: 'application/json', size: text.length }, text);
  assert.equal(r.ok, true);
  assert.deepEqual(JSON.parse(JSON.stringify(r.state)), s); // JSON hop: the vm context has its own Object.prototype
  assert.equal(P.exportJson(r.state), text, 're-export is byte-identical');
});

test('import accepts .json with empty or text/plain type and BOM-prefixed text', () => {
  const P = loadWithFiles(), text = '﻿' + P.exportJson(sampleState());
  assert.equal(read(P, { name: 'a.JSON', type: '', size: text.length }, text).ok, true);
  assert.equal(read(P, { name: 'a.json', type: 'text/plain', size: 10 }, text).ok, true);
});

test('import rejects wrong type without reading', () => {
  const P = loadWithFiles();
  let reads = 0, out;
  const FR = class { readAsText() { reads++; } };
  P.files.readSaveFile({ name: 'pic.png', type: 'image/png', size: 5 }, (r) => { out = r; }, { FileReader: FR });
  assert.equal(out.ok, false); assert.equal(out.code, 'type'); assert.equal(reads, 0);
  assert.equal(read(P, { name: 'save.txt', type: 'text/plain', size: 5 }, '{}').code, 'type');
  assert.equal(read(P, null, '').code, 'nofile');
});

test('import rejects oversize by declared size and by content length', () => {
  const P = loadWithFiles();
  assert.equal(read(P, { name: 'a.json', type: 'application/json', size: P.files.MAX_IMPORT_BYTES + 1 }, '{}').code, 'size');
  assert.equal(read(P, JSON_FILE, 'x'.repeat(50), undefined, { maxBytes: 20 }).code, 'size');
});

test('import rejects corrupt JSON, wrong documents and read failures as error results', () => {
  const P = loadWithFiles();
  assert.equal(read(P, JSON_FILE, '{not json').code, 'parse');
  assert.equal(read(P, JSON_FILE, '{"hello":1}').ok, false);
  assert.equal(read(P, JSON_FILE, '[1,2]').ok, false);
  assert.equal(read(P, JSON_FILE, undefined).code, 'read');
  assert.equal(read(P, JSON_FILE, '', 'error').code, 'read');
  assert.equal(read(P, JSON_FILE, '', 'abort').code, 'read');
  let r; P.files.readSaveFile(JSON_FILE, (x) => { r = x; }, {});
  assert.equal(r.code, 'unsupported');
});

test('import is pure: it never calls saveGame/deleteSave and never mutates the file object', () => {
  const P = loadWithFiles(), f = Object.freeze({ name: 'a.json', type: 'application/json', size: 10 });
  let writes = 0;
  P.saveGame = () => { writes++; }; P.deleteSave = () => { writes++; };
  assert.equal(read(P, f, '{bad').ok, false);
  assert.equal(read(P, f, 'x'.repeat(50), undefined, { maxBytes: 20 }).ok, false);
  assert.equal(read(P, f, P.exportJson(sampleState())).ok, true);
  assert.equal(writes, 0);
});

test('downloadText clicks an anchor with the given name and revokes the URL', () => {
  const P = loadWithFiles();
  const d = fakeDom();
  const r = P.files.downloadText('x.json', '{"a":1}', d.env);
  assert.deepEqual({ ...r }, { ok: true, name: 'x.json', bytes: 7 });
  assert.deepEqual(d.log.clicked, [{ href: 'blob:fake/1', download: 'x.json' }]);
  assert.deepEqual(d.log.revoked, ['blob:fake/1']);
  assert.equal(P.files.downloadText('x.json', '{}', {}).code, 'unsupported');
});

function fakePicker(files, readerMode) {
  const handlers = {};
  const input = { style: {}, addEventListener: (t, fn) => { handlers[t] = fn; }, click() {}, files };
  return {
    handlers, input,
    env: { document: { createElement: (t) => { assert.equal(t, 'input'); return input; } }, FileReader: fakeReader('{"ok":true}', readerMode) }
  };
}

test('pickSaveText resolves the chosen file text, null on cancel or empty selection', async () => {
  const P = loadWithFiles();
  const picked = fakePicker([JSON_FILE]);
  const pending = P.files.pickSaveText(picked.env);
  picked.handlers.change();
  assert.equal(await pending, '{"ok":true}');
  assert.equal(picked.input.type, 'file');

  const cancelled = fakePicker([]);
  const p2 = P.files.pickSaveText(cancelled.env);
  cancelled.handlers.cancel();
  assert.equal(await p2, null);

  const empty = fakePicker([]);
  const p3 = P.files.pickSaveText(empty.env);
  empty.handlers.change();
  assert.equal(await p3, null);
});

test('pickSaveText rejects wrong type, read errors and a missing DOM', async () => {
  const P = loadWithFiles();
  const wrong = fakePicker([{ name: 'pic.png', type: 'image/png', size: 5 }]);
  const p1 = P.files.pickSaveText(wrong.env);
  wrong.handlers.change();
  await assert.rejects(p1, /not a JSON save file/);

  const bad = fakePicker([JSON_FILE], 'error');
  const p2 = P.files.pickSaveText(bad.env);
  bad.handlers.change();
  await assert.rejects(p2, /could not read file/);

  await assert.rejects(P.files.pickSaveText({ document: null, FileReader: null }), /not available/);
});
