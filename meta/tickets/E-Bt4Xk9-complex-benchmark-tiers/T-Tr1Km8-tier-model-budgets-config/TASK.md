# TASK: T-Tr1Km8-tier-model-budgets-config

## Metadata
- Task ID: `T-Tr1Km8-tier-model-budgets-config`
- Epic ID: `E-Bt4Xk9-complex-benchmark-tiers`
- Owner: developer agent
- Created: 2026-07-22
- Last Updated: 2026-07-22
- Status: Draft
- Estimate: 1.5 days

## Requirements Mapping
- FR-1 (tier model + per-tier defaults config)

## Description
Introduce the benchmark **tier** concept. Add an optional `tier` enum to the suite schema (default `small`, so `dev-core` is byte-unchanged), a committed `benchmarks/tiers.json` holding per-tier default caps/knobs, and a small `bench/tiers.py` loader that resolves a suite's tier to its effective config with `CLI > tier-default > builtin` precedence. This is the foundation every other task consumes (budget caps, default parallelism, default timeout, xlarge-disabled gate).

## File ownership (exclusive — no other task edits these)
- `benchmarks/schemas/benchmark-suite.schema.json` — add optional `tier` property (this task only in Wave A; T-Wp4Nz5 later adds a `source` property in a different region — sequenced after this task).
- `src/agent_orchestrator/bench/spec.py` — add `tier: str = "small"` to `BenchSuite`; validate against known tiers. (Wave-A exclusive; T-Wp4Nz5/T-Sg6Jf2 edit spec.py later, sequenced after.)
- `benchmarks/tiers.json` — NEW (committed).
- `src/agent_orchestrator/bench/tiers.py` — NEW module.
- (read-only) `benchmarks/suites/dev-core/suite.json` — confirm it still validates unedited.

## Inputs / Outputs
- Inputs: a `suite.json`'s `tier` (optional); `benchmarks/tiers.json`; optional CLI overrides (resolved by callers, not here).
- Outputs: a validated `BenchSuite` carrying `tier`; a `TierConfig` object from `tiers.py` with `{enabled, cost_budget_usd_per_run, cost_budget_usd_per_subject, default_max_parallel, default_timeout_seconds}`.

## Acceptance Criteria
1. **Given** `dev-core/suite.json` (no `tier` field) **When** `ao-bench validate --suite` runs **Then** it validates OK and the loaded `BenchSuite.tier == "small"` — the committed file is byte-unchanged (git diff empty for `dev-core`).
2. **Given** a suite with `tier: "medium"` **When** loaded **Then** `BenchSuite.tier == "medium"`; **Given** `tier: "gigantic"` **Then** `load_suite` raises `SpecValidationError` naming the bad tier and listing the four valid tiers.
3. **Given** `benchmarks/tiers.json` **When** `tiers.load_tier_config("large")` is called **Then** it returns `cost_budget_usd_per_run==800`, `cost_budget_usd_per_subject==100`, `enabled==True`; `load_tier_config("xlarge").enabled==False`.
4. **Given** a malformed/missing `tiers.json` **When** `load_tier_config` is called **Then** it raises a structured `SpecValidationError`/`BenchError` naming the file (never a raw traceback).
5. `resolve_effective(tier, cli_max_parallel=None, cli_cost_budget=None, cli_timeout=None)` returns the CLI value when given, else the tier default, else a documented builtin fallback — unit-tested for all three precedence branches.
6. SI-1: `agent_orchestrator.bench` still absent from `sys.modules` after importing core `agent_orchestrator.cli` (grep + import test unchanged).

## Risks
- Making `tier` required would force an edit to `dev-core` and every existing subject/test — **must** stay optional-with-default. (Mitigation: default `"small"`, `additionalProperties:false` still holds since we add the property.)

## Pseudocode / Algorithm
```text
# benchmark-suite.schema.json (add under "properties")
"tier": { "enum": ["small","medium","large","xlarge"], "default": "small",
          "description": "Benchmark tier; drives per-tier budget/parallel/timeout defaults from benchmarks/tiers.json." }

# bench/spec.py
KNOWN_TIERS = frozenset({"small","medium","large","xlarge"})
class BenchSuite(BaseModel):
    ...
    tier: str = "small"
# in load_suite(), after model construct:
IF suite.tier NOT IN KNOWN_TIERS: RAISE SpecValidationError(f"unknown tier {suite.tier!r}; known: {sorted(KNOWN_TIERS)}", path="tier")

# bench/tiers.py
TIERS_FILE = REPO_ROOT / "benchmarks" / "tiers.json"
class TierConfig(BaseModel):
    enabled: bool
    cost_budget_usd_per_run: float
    cost_budget_usd_per_subject: float
    default_max_parallel: int
    default_timeout_seconds: int
_BUILTIN_FALLBACK = TierConfig(enabled=True, cost_budget_usd_per_run=10, cost_budget_usd_per_subject=5,
                               default_max_parallel=1, default_timeout_seconds=1800)
FUNCTION load_tier_config(tier: str, path=TIERS_FILE) -> TierConfig:
    IF not path.is_file(): RAISE SpecValidationError(f"tiers config not found: {path}")
    data = read_json(path)                       # guard JSONDecodeError -> SpecValidationError
    IF tier not in data["tiers"]: RAISE SpecValidationError(f"tier {tier!r} not in {path}")
    RETURN TierConfig(**data["tiers"][tier])
FUNCTION resolve_effective(tc: TierConfig, *, cli_max_parallel, cli_cost_budget, cli_timeout) -> dict:
    RETURN { max_parallel: cli_max_parallel or tc.default_max_parallel,
             cost_budget_usd: cli_cost_budget if cli_cost_budget is not None else tc.cost_budget_usd_per_subject,
             timeout: cli_timeout or tc.default_timeout_seconds }
```

## Schemas / Interface Notes
- `benchmarks/tiers.json` (committed):
```json
{ "version": "1.0",
  "tiers": {
    "small":  { "enabled": true,  "cost_budget_usd_per_run": 10,  "cost_budget_usd_per_subject": 5,   "default_max_parallel": 1, "default_timeout_seconds": 900 },
    "medium": { "enabled": true,  "cost_budget_usd_per_run": 150, "cost_budget_usd_per_subject": 50,  "default_max_parallel": 4, "default_timeout_seconds": 3600 },
    "large":  { "enabled": true,  "cost_budget_usd_per_run": 800, "cost_budget_usd_per_subject": 100, "default_max_parallel": 3, "default_timeout_seconds": 5400 },
    "xlarge": { "enabled": false, "cost_budget_usd_per_run": 8000,"cost_budget_usd_per_subject": 1000,"default_max_parallel": 4, "default_timeout_seconds": 7200 }
  } }
```
- Interface: `bench.tiers.load_tier_config(tier) -> TierConfig`; `bench.tiers.resolve_effective(...)`. Triggers/events: N/A. Artifacts: `benchmarks/tiers.json`.

## Handoff Boundary
- Upstream: none.
- Downstream: T-Bg2Wq4 (per-subject cap default), T-Pl3Rx7 (default_max_parallel), T-Cm9Tb4 (per-run cap + xlarge gate), T-Wp4Nz5/T-Sw5Hd9/T-Sg6Jf2/T-Md7Vc3 (tier field on their suites). Do NOT wire the caps into the runner here — this task only provides the config + loader.

## Artifacts
- Docs/comments: this folder.
- Large outputs: none.
