'use strict';

// Tests for the JS load order in scripts/build.mjs (docs/INTEGRATION-PLAN.md §5): no duplicate entries,
// every entry exists or is pending, and `--verify` fails on unlisted or missing src files.

const test = require('node:test');
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const { copyFileSync, existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } = require('node:fs');
const { tmpdir } = require('node:os');
const { join, resolve } = require('node:path');
const { pathToFileURL } = require('node:url');

const PROJECT_ROOT = resolve(__dirname, '..', '..');
const BUILD = join(PROJECT_ROOT, 'scripts', 'build.mjs');

function loadBuild() {
  return import(pathToFileURL(BUILD).href);
}

function runVerify(script) {
  const result = spawnSync(process.execPath, [script, '--verify'], { encoding: 'utf8' });
  return { status: result.status, stdout: result.stdout, stderr: result.stderr };
}

test('JS_ORDER has no duplicate entries', async () => {
  const { JS_ORDER } = await loadBuild();
  const seen = new Set();
  const duplicates = JS_ORDER.filter((file) => {
    const repeated = seen.has(file);
    seen.add(file);
    return repeated;
  });
  assert.deepEqual(duplicates, []);
});

test('every JS_ORDER entry exists on disk or is in PENDING_JS', async () => {
  const { JS_ORDER, PENDING_JS } = await loadBuild();
  const unresolved = JS_ORDER.filter((file) => !existsSync(join(PROJECT_ROOT, file)) && !PENDING_JS.includes(file));
  assert.deepEqual(unresolved, []);
});

test('every PENDING_JS entry is also listed in JS_ORDER', async () => {
  const { JS_ORDER, PENDING_JS } = await loadBuild();
  const unlisted = PENDING_JS.filter((file) => !JS_ORDER.includes(file));
  assert.deepEqual(unlisted, []);
});

test('findJsOrderProblems reports duplicates, unlisted files and missing files', async () => {
  const { findJsOrderProblems } = await loadBuild();
  const order = ['src/a.js', 'src/a.js', 'src/b.js', 'src/c.js'];
  const onDisk = ['src/a.js', 'src/b.js', 'src/d.js'];

  assert.deepEqual(findJsOrderProblems(onDisk, order, ['src/c.js']), {
    duplicates: ['src/a.js'],
    unlisted: ['src/d.js'],
    missing: [],
  });
  assert.deepEqual(findJsOrderProblems(onDisk, order, []).missing, ['src/c.js']);
});

test('build --verify passes on the shipped tree', () => {
  const result = runVerify(BUILD);
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /verify: JS_ORDER ok/);
});

test('build --verify fails when src/ has a file that is not in JS_ORDER', () => {
  const fixture = mkdtempSync(join(tmpdir(), 'build-order-'));
  try {
    mkdirSync(join(fixture, 'scripts'));
    mkdirSync(join(fixture, 'src'));
    copyFileSync(BUILD, join(fixture, 'scripts', 'build.mjs'));
    writeFileSync(join(fixture, 'src', 'rogue.js'), '// not listed anywhere\n');

    const result = runVerify(join(fixture, 'scripts', 'build.mjs'));
    assert.equal(result.status, 1);
    assert.match(result.stderr, /in src\/ but not in JS_ORDER: src\/rogue\.js/);
  } finally {
    rmSync(fixture, { recursive: true, force: true });
  }
});
