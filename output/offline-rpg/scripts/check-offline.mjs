// Offline guard for the offline RPG: fails when source, styles, markup or the built
// dist/ reference anything off-device (remote URLs, CDNs/fonts, network APIs, dynamic import).
//
// Usage: node scripts/check-offline.mjs [path ...]
//   Paths are files or directories. With no paths, scans the default roots under the
//   project root (src/, styles/, index.html, dist/); missing default roots are skipped.
// Exit: 0 clean · 1 findings (file:line: rule: match) · 2 bad usage / unreadable path.

import { readdirSync, readFileSync, statSync, existsSync } from 'node:fs';
import { join, relative, resolve, dirname, extname } from 'node:path';
import { fileURLToPath } from 'node:url';

const PROJECT_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const DEFAULT_ROOTS = ['src', 'styles', 'index.html', 'dist'];
const SCANNED_EXTENSIONS = new Set(['.js', '.mjs', '.cjs', '.html', '.htm', '.css', '.svg', '.xml', '.json']);
const SKIPPED_DIRS = new Set(['node_modules', '.git']);

// XML/SVG namespace identifiers are compared as strings, never fetched. Exact matches only.
const ALLOWED_NAMESPACE_URIS = new Set([
  'http://www.w3.org/2000/svg',
  'http://www.w3.org/1999/xhtml',
  'http://www.w3.org/1999/xlink',
  'http://www.w3.org/XML/1998/namespace',
]);

// Known font/CDN host names. Matched as substrings, case-insensitively.
const CDN_HOST_NAMES = [
  'fonts.googleapis', 'fonts.gstatic', 'googleapis.com', 'cdnjs', 'unpkg.com', 'jsdelivr',
  'bootstrapcdn', 'typekit', 'use.fontawesome', 'cloudflare.com/ajax', 'jquery.com',
];

const URL_PATTERN = /\b(?:https?|wss?|ftp):\/\/[^\s"'`<>)]*/gi;
const PROTOCOL_RELATIVE_PATTERN = /(?:^|[\s"'`(=,])\/\/[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+/g;

const RULES = [
  { id: 'no-remote-url', pattern: URL_PATTERN, allowed: (match) => ALLOWED_NAMESPACE_URIS.has(match) },
  { id: 'no-protocol-relative', pattern: PROTOCOL_RELATIVE_PATTERN },
  { id: 'no-css-import', pattern: /@import\b/gi },
  { id: 'no-network-api', pattern: /\bfetch\s*\(|\bXMLHttpRequest\b|\bWebSocket\b|\bEventSource\b|\bsendBeacon\b/g },
  { id: 'no-dynamic-import', pattern: /\bimport\s*\(/g },
  { id: 'no-cdn-or-font-host', pattern: new RegExp(CDN_HOST_NAMES.map(escapeRegExp).join('|'), 'gi') },
];

function escapeRegExp(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

// Returns findings for one text payload: [{ line, rule, match }] in file order.
export function scanText(text) {
  const findings = [];
  text.split(/\r?\n/).forEach((lineText, index) => {
    for (const rule of RULES) {
      for (const hit of lineText.matchAll(rule.pattern)) {
        const match = hit[0].trim();
        if (rule.allowed?.(match)) continue;
        findings.push({ line: index + 1, rule: rule.id, match });
      }
    }
  });
  return findings;
}

// Expands a list of files/directories into scannable file paths, sorted for stable output.
export function collectFiles(paths) {
  const files = [];
  const walk = (current) => {
    const stats = statSync(current);
    if (stats.isDirectory()) {
      for (const entry of readdirSync(current).sort()) {
        if (SKIPPED_DIRS.has(entry)) continue;
        walk(join(current, entry));
      }
    } else if (SCANNED_EXTENSIONS.has(extname(current).toLowerCase())) {
      files.push(current);
    }
  };
  for (const path of paths) walk(path);
  return files;
}

// Scans the given paths and returns [{ file, line, rule, match }] across all files.
export function scanPaths(paths) {
  const findings = [];
  for (const file of collectFiles(paths)) {
    const text = readFileSync(file, 'utf8');
    for (const finding of scanText(text)) {
      findings.push({ file, ...finding });
    }
  }
  return findings;
}

function runCli(argv) {
  const explicit = argv.slice(2);
  const targets = explicit.length
    ? explicit.map((arg) => resolve(arg))
    : DEFAULT_ROOTS.map((root) => join(PROJECT_ROOT, root)).filter((path) => existsSync(path));

  const missing = explicit.map((arg) => resolve(arg)).filter((path) => !existsSync(path));
  if (missing.length) {
    for (const path of missing) console.error(`check-offline: no such path: ${path}`);
    return 2;
  }

  let findings;
  try {
    findings = scanPaths(targets);
  } catch (error) {
    console.error(`check-offline: ${error.message}`);
    return 2;
  }

  for (const finding of findings) {
    console.log(`${relative(process.cwd(), finding.file)}:${finding.line}: ${finding.rule}: ${finding.match}`);
  }
  if (findings.length) {
    console.error(`check-offline: ${findings.length} offline violation(s)`);
    return 1;
  }
  return 0;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = runCli(process.argv);
}
