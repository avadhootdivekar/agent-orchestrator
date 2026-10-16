// Runs every project check in order (build, tests, offline scan) and prints one summary line per step.
//
// Usage: node scripts/run-all-checks.mjs   (or: npm run check-all)
// Each step runs its npm script, so this file stays in step with package.json. All steps run even when
// an earlier one fails, so one run reports the full state. Child output is shown only for failed steps.
// Exit: 0 when every step passes · 1 when any step fails.
// Zero dependencies; node >= 18.

import { spawnSync } from 'node:child_process';

const STEPS = [
  { name: 'build', script: 'build' },
  { name: 'test', script: 'test' },
  { name: 'check-offline', script: 'check-offline' },
];

// npm is a .cmd shim on Windows, which spawn cannot run without a shell.
const NPM_COMMAND = process.platform === 'win32' ? 'npm.cmd' : 'npm';

function runStep(step) {
  const started = Date.now();
  const result = spawnSync(NPM_COMMAND, ['run', step.script], { encoding: 'utf8' });
  const seconds = ((Date.now() - started) / 1000).toFixed(1);
  // A spawn failure (npm missing) leaves status null with result.error set; treat it as a failure too.
  const passed = result.status === 0;
  const detail = passed ? '' : ` (exit ${result.status ?? 'none'}${result.error ? `, ${result.error.message}` : ''})`;
  console.log(`${passed ? 'PASS' : 'FAIL'} ${step.name} ${seconds}s${detail}`);
  if (!passed) {
    process.stdout.write(result.stdout ?? '');
    process.stderr.write(result.stderr ?? '');
  }
  return passed;
}

const results = STEPS.map(runStep);
const failed = results.filter((passed) => !passed).length;
console.log(failed === 0 ? `all ${STEPS.length} checks passed` : `${failed} of ${STEPS.length} checks failed`);
process.exitCode = failed === 0 ? 0 : 1;
