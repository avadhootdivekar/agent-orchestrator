// Suite entry for `node --test tests/scripts`: Node 22 runs a directory argument as a module,
// so this shim loads every test file in this folder (check-offline, build-order, boot).
await import('./check-offline.test.mjs');
await import('./build-order.test.js');
await import('./boot.test.js');
await import('./bench.test.js');
