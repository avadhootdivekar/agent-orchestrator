// Single-file build: inlines styles/*.css and src/**/*.js into one classic-script dist/index.html.
//
// Usage: node scripts/build.mjs            write dist/index.html, then run the offline guard on it
//        node scripts/build.mjs --verify   build twice in memory and fail unless the output is byte-identical
//
// Deterministic: inputs are read in sorted/ordered sequence and the output carries no timestamps.
// Zero dependencies; node >= 18.

import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const TEMPLATE_FILE = join(ROOT, 'index.html');
const STYLES_DIR = join(ROOT, 'styles');
const SRC_DIR = join(ROOT, 'src');
const DIST_DIR = join(ROOT, 'dist');
const OUT_FILE = join(DIST_DIR, 'index.html');
const OFFLINE_GUARD = join(ROOT, 'scripts', 'check-offline.mjs');

const CSS_MARKER = '<!--@CSS-->';
const JS_MARKER = '<!--@JS-->';

// JS load order, mirroring the load order in docs/ARCHITECTURE.md §2. Every module is an IIFE that
// attaches to window.RPG, so this order only has to satisfy load-time dependencies. Listed files
// that do not exist yet are skipped. Files on disk that are not listed are appended after the
// ordered ones, in sorted order, with a warning on stderr.
const JS_ORDER = [
  'src/core/namespace.js', 'src/core/util.js', 'src/core/rng.js', 'src/core/events.js', 'src/core/pool.js',
  'src/core/entities.js', 'src/core/state.js',
  'src/data/maps/test-map.js',
  'src/data/difficulties.js', 'src/data/weapons.js', 'src/data/skills.js', 'src/data/monsters.js',
  'src/data/items.js', 'src/data/areas.js', 'src/data/stats.js', 'src/data/keymap.js',
  'src/world/stats.js', 'src/world/tilemap.js', 'src/world/collision.js', 'src/world/spatial.js',
  'src/world/mapgen.js', 'src/world/world.js', 'src/world/combat.js', 'src/world/weapons.js',
  'src/world/skills.js', 'src/world/skill-effects.js', 'src/world/ai.js', 'src/world/boss.js', 'src/world/loot.js',
  'src/world/equipment.js', 'src/world/progression.js', 'src/world/difficulty.js',
  'src/world/encounters.js', 'src/world/areas.js', 'src/world/sim.js',
  'src/sim/player.js', 'src/sim/sim.js',
  'src/persistence/save.js', 'src/persistence/migrate.js', 'src/persistence/storage.js',
  'src/persistence/exportimport.js', 'src/persistence/snapshot.js',
  'src/input/keyboard.js', 'src/input/mouse.js', 'src/input/keymap.js', 'src/input/raw.js',
  'src/input/intent.js', 'src/input/input.js',
  'src/audio/audio.js',
  'src/render/palette.js', 'src/render/camera.js', 'src/render/draw-shapes.js',
  'src/render/draw-entities.js', 'src/render/draw-map.js', 'src/render/draw-effects.js',
  'src/render/hud.js', 'src/render/renderer.js',
  'src/ui/screens.js', 'src/ui/inventory.js', 'src/ui/character.js', 'src/ui/skills-screen.js',
  'src/ui/settings.js', 'src/ui/saveload.js', 'src/ui/pause.js', 'src/ui/mainmenu.js',
  'src/core/loop.js', 'src/core/game.js',
  'src/main.js',
];

// Recursively lists files with the given extension as project-relative POSIX paths, sorted by name.
function listFiles(dir, extension) {
  const found = [];
  const walk = (current) => {
    const entries = readdirSync(current, { withFileTypes: true })
      .sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
    for (const entry of entries) {
      const full = join(current, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (entry.name.endsWith(extension)) found.push(relative(ROOT, full).split(sep).join('/'));
    }
  };
  if (existsSync(dir)) walk(dir);
  return found;
}

function orderedJsFiles() {
  const onDisk = listFiles(SRC_DIR, '.js');
  const ordered = JS_ORDER.filter((file) => onDisk.includes(file));
  const unlisted = onDisk.filter((file) => !JS_ORDER.includes(file));
  if (unlisted.length > 0) {
    process.stderr.write(`warning: not in JS_ORDER, appended last: ${unlisted.join(', ')}\n`);
  }
  return [...ordered, ...unlisted];
}

function buildCss() {
  const css = listFiles(STYLES_DIR, '.css')
    .map((file) => readFileSync(join(ROOT, file), 'utf8'))
    .join('\n');
  if (/<\/style/i.test(css)) throw new Error('styles contain "</style", which would end the inline <style> block');
  return css;
}

function buildJs() {
  return orderedJsFiles()
    .map((file) => {
      const source = readFileSync(join(ROOT, file), 'utf8');
      // "<!--" inside a classic script can switch the parser into its escaped state; refuse rather than rewrite.
      if (source.includes('<!--')) throw new Error(`${file} contains "<!--", which is not allowed inside an inline script`);
      return `/* ==== ${file} ==== */\n${source}\n;\n`;
    })
    .join('')
    .replace(/<\/script/gi, '<\\/script');
}

function buildHtml() {
  const template = readFileSync(TEMPLATE_FILE, 'utf8');
  for (const marker of [CSS_MARKER, JS_MARKER]) {
    if (template.split(marker).length !== 2) throw new Error(`index.html must contain ${marker} exactly once`);
  }
  // Replacer functions keep "$" sequences in the inlined code from being read as replacement patterns.
  return template
    .replace(CSS_MARKER, () => `<style>\n${buildCss()}\n</style>`)
    .replace(JS_MARKER, () => `<script>\n${buildJs()}\n</script>`);
}

function main() {
  const mode = process.argv[2];

  if (mode === '--verify') {
    const first = buildHtml();
    const second = buildHtml();
    if (first !== second) throw new Error('build is not deterministic: two builds differ');
    console.log(`verify: two in-memory builds are byte-identical (${first.length} bytes)`);
    return;
  }

  if (mode !== undefined) throw new Error(`unknown argument "${mode}" (use no argument or --verify)`);

  const html = buildHtml();
  mkdirSync(DIST_DIR, { recursive: true });
  writeFileSync(OUT_FILE, html);
  console.log(`built ${relative(ROOT, OUT_FILE)} (${html.length} bytes)`);

  // The offline guard owns the remote-reference rules; a finding fails the build.
  execFileSync(process.execPath, [OFFLINE_GUARD, OUT_FILE], { stdio: 'inherit' });
}

try {
  main();
} catch (err) {
  console.error(`build failed: ${err.message}`);
  process.exitCode = 1;
}
