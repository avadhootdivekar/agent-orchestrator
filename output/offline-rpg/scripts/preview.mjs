// Static preview server for dist/ (node:http only, no dependencies).
//
// Usage: node scripts/preview.mjs [--port 8777] [--host 127.0.0.1]
// Serves dist/ over plain HTTP so the game can be played from a local static server (the README
// states when file:// is enough and when a server is needed). Binds loopback unless --host is given.

import { createReadStream, existsSync, statSync } from 'node:fs';
import { createServer } from 'node:http';
import { dirname, extname, join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const DIST_DIR = join(ROOT, 'dist');
const DEFAULT_PORT = 8777;
const DEFAULT_HOST = '127.0.0.1';

const CONTENT_TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
};

function parseArgs(argv) {
  const options = { port: DEFAULT_PORT, host: DEFAULT_HOST };
  for (let i = 0; i < argv.length; i += 1) {
    const flag = argv[i];
    if (flag === '--port' || flag === '--host') {
      const value = argv[i + 1];
      if (value === undefined) throw new Error(`${flag} needs a value`);
      i += 1;
      if (flag === '--port') {
        const port = Number(value);
        if (!Number.isInteger(port) || port < 0 || port > 65535) throw new Error(`invalid port "${value}"`);
        options.port = port;
      } else {
        options.host = value;
      }
    } else {
      throw new Error(`unknown argument "${flag}"`);
    }
  }
  return options;
}

function send(res, status, body) {
  res.writeHead(status, { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'no-store' });
  res.end(body);
}

function handle(req, res) {
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    res.writeHead(405, { Allow: 'GET, HEAD' });
    res.end();
    return;
  }

  let pathname;
  try {
    pathname = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
  } catch {
    send(res, 400, 'bad request');
    return;
  }
  if (pathname.endsWith('/')) pathname += 'index.html';

  // Path-traversal guard: the resolved file must stay inside dist/.
  const file = resolve(DIST_DIR, `.${pathname}`);
  if (!file.startsWith(DIST_DIR + sep)) {
    send(res, 403, 'forbidden');
    return;
  }

  let stats;
  try {
    stats = statSync(file);
  } catch {
    send(res, 404, 'not found (run "npm run build" first if dist/ is missing)');
    return;
  }
  if (!stats.isFile()) {
    send(res, 404, 'not found');
    return;
  }

  res.writeHead(200, {
    'Content-Type': CONTENT_TYPES[extname(file)] ?? 'application/octet-stream',
    'Content-Length': stats.size,
    'Cache-Control': 'no-store',
  });
  if (req.method === 'HEAD') {
    res.end();
    return;
  }
  createReadStream(file).pipe(res);
}

function main() {
  const { port, host } = parseArgs(process.argv.slice(2));
  if (!existsSync(DIST_DIR)) {
    process.stderr.write('warning: dist/ does not exist yet; run "npm run build" first\n');
  }
  const server = createServer(handle);
  server.on('error', (err) => {
    console.error(`preview failed: ${err.message}`);
    process.exitCode = 1;
  });
  server.listen(port, host, () => {
    const address = server.address();
    console.log(`serving dist/ at http://${host}:${address.port}/ (Ctrl+C to stop)`);
  });
}

main();
