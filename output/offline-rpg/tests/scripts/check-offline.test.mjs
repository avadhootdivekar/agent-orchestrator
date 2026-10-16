// Tests for scripts/check-offline.mjs: fixture-driven CLI checks plus direct scanText unit checks.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { scanText } from '../../scripts/check-offline.mjs';

const TESTS_DIR = dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = resolve(TESTS_DIR, '..', '..');
const SCANNER = join(PROJECT_ROOT, 'scripts', 'check-offline.mjs');
const FIXTURES = join(TESTS_DIR, '..', 'fixtures', 'offline');

function runScanner(args) {
  const result = spawnSync(process.execPath, [SCANNER, ...args], { encoding: 'utf8', cwd: PROJECT_ROOT });
  return { status: result.status, stdout: result.stdout, stderr: result.stderr };
}

test('clean fixture passes with exit 0 and no findings', () => {
  const clean = ['clean.js', 'clean.html'].map((name) => join(FIXTURES, name));
  const result = runScanner(clean);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout, '');
});

const VIOLATING_FIXTURES = {
  'remote-url.js': { rule: 'no-remote-url', line: 1 },
  'protocol-relative.html': { rule: 'no-protocol-relative', line: 1 },
  'css-import.css': { rule: 'no-css-import', line: 1 },
  'link-and-img.html': { rule: 'no-remote-url', line: 1 },
  'network-api.js': { rule: 'no-network-api', line: 1 },
  'dynamic-import.js': { rule: 'no-dynamic-import', line: 2 },
  'cdn-name.js': { rule: 'no-cdn-or-font-host', line: 1 },
};

for (const [name, expected] of Object.entries(VIOLATING_FIXTURES)) {
  test(`violating fixture ${name} fails with file:line output`, () => {
    const result = runScanner([join(FIXTURES, name)]);
    assert.equal(result.status, 1, result.stderr);
    const lines = result.stdout.trim().split('\n');
    const hit = lines.find((line) => line.includes(`${name}:${expected.line}: ${expected.rule}:`));
    assert.ok(hit, `expected ${expected.rule} at ${name}:${expected.line} in:\n${result.stdout}`);
  });
}

test('every violating fixture is reported as a failure, covering every rule', () => {
  const result = runScanner(Object.keys(VIOLATING_FIXTURES).map((name) => join(FIXTURES, name)));
  assert.equal(result.status, 1);
  for (const { rule } of Object.values(VIOLATING_FIXTURES)) {
    assert.match(result.stdout, new RegExp(`: ${rule}: `), `missing rule ${rule}`);
  }
});

test('scanner exits 0 on the project tree with no explicit paths', () => {
  const result = runScanner([]);
  assert.equal(result.status, 0, `${result.stdout}${result.stderr}`);
});

test('explicit missing path is a usage error (exit 2), not a silent pass', () => {
  const result = runScanner([join(FIXTURES, 'does-not-exist.js')]);
  assert.equal(result.status, 2);
  assert.match(result.stderr, /no such path/);
});

test('SVG/XML namespace URIs are allowed', () => {
  const text = [
    '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">',
    'document.createElementNS("http://www.w3.org/2000/svg", "g")',
  ].join('\n');
  assert.deepEqual(scanText(text), []);
});

test('w3.org URIs that are not on the namespace allow-list are still flagged', () => {
  const findings = scanText('const x = "http://www.w3.org/2000/svgx";');
  assert.equal(findings.length, 1);
  assert.equal(findings[0].rule, 'no-remote-url');
});

test('scanText reports 1-based line numbers and matched text', () => {
  const findings = scanText('ok\nconst a = "https://example.com";\nfetch("/x")');
  assert.deepEqual(
    findings.map(({ line, rule }) => ({ line, rule })),
    [
      { line: 2, rule: 'no-remote-url' },
      { line: 3, rule: 'no-network-api' },
    ],
  );
  assert.equal(findings[0].match, 'https://example.com');
});

test('line comments and plain code without remote references are not flagged', () => {
  const text = [
    '// fetch the save later, see the design doc',
    'const prefetchCount = 3;',
    'const path = "/local/assets/tile.png";',
  ].join('\n');
  assert.deepEqual(scanText(text), []);
});
