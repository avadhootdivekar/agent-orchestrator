// Suite entry for `node --test tests/scripts`: Node 22 runs a directory argument as a module,
// so this shim loads every *.test.mjs in this folder (currently just check-offline).
await import('./check-offline.test.mjs');
