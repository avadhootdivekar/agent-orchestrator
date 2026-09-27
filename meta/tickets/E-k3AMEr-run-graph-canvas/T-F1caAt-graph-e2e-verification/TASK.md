# TASK: T-F1caAt-graph-e2e-verification

## Metadata
- Task ID: `T-F1caAt-graph-e2e-verification`
- Epic ID: `E-k3AMEr-run-graph-canvas`
- Owner: `tester` (executed with Dev A)
- Created: `2026-09-27`
- Last Updated: `2026-09-27`
- Status: `Draft`
- Estimate: `14 focus hours (< 2 days)`

## Requirements Mapping
- Requirement IDs: U-1..U-5 (end to end), D-1, D-2, D-6, NFR-3 · HLD §18, §19 · Assumption A-5 / Risk R-8

## Description
Prove the whole stack from the outermost boundary, then gather the evidence the pure-level tests
cannot provide:

1. `tests/ui/graph_fixtures.py::write_synthetic_run(root, waves, fanout, *, clock)` writes a
   deterministic run dir (`state.json` with `spawned_by`/`spec_sessions`, plus the snapshot) with an
   overseer-like shape (checkpoint → `fanout` units + next checkpoint, repeated `waves` times). This
   is reusable for manual UI work.
2. **E2E (pytest, real processes)**, following `tests/ui/test_e2e_ui.py`:
   - (a) A real uvicorn `ao ui` subprocess, plus a real `ao run` (fake executor) of a workflow with
     an `emit_tasks` step and a 2-iteration loop. Then GET `/api/runs/{id}/graph` over HTTP and
     assert the exact spawn edge set and dependency edge set, and that they differ.
   - (b) Bare `ao run` via `CliRunner` (no dashboard involved) produces `spec_sessions` plus a
     snapshot file (D-2).
3. **Browser smoke** (`@pytest.mark.browser`). It uses **Python Playwright driving the system
   Chrome** (`channel="chrome"`, so there is no browser download). Add
   `browser = ["playwright>=1.45"]` as a new optional extra in `pyproject.toml`, mirroring the
   existing `swebench`/`ui` extras pattern. The test is **skipped** when `playwright` is not
   importable or Chrome is not found.

   Why Playwright and not `chrome --dump-dom`: the dashboard has **no URL routing** (`App.tsx` is
   state-based), so reaching a run's Graph tab needs clicks. And `--dump-dom` cannot observe console
   or CSP events (tester MUST-FIX).

   Flow, against `ao ui` on a `write_synthetic_run(waves=20, fanout=8)` workspace (≈ 180 nodes),
   **under the real SPA CSP** served by the app:
   - (i) `page.add_init_script` registers a `securitypolicyviolation` listener that pushes onto
     `window.__cspViolations`, and `page.on("console")` and `page.on("pageerror")` collect errors.
   - (ii) Goto `/`, click the run row, click the **Graph** tab, and wait for `.react-flow__node`.
     Assert the count == the expected node count.
   - (iii) Click **"Spawned by"**, then wait and assert that the node set is unchanged and the edge
     DOM (`.react-flow__edge`) count == the fixture's spawn edge count. Real browser edge rendering
     covers risk R-3.
   - (iv) Click a node and assert the panel `role="complementary"` shows its label.
   - (v) Assert `window.__cspViolations` is empty, and there are **zero** console errors and zero
     `pageerror`s.
   - (vi) **Negative control.** Serve a fixture HTML page from the test server with the same
     `SPA_CSP` header and an inline `<script>`. The same listener **must** record ≥ 1 violation.
     This proves the detector detects.
   - (vii) `page.screenshot` of the dependency view, the spawn view, and the panel open, saved to
     `output/E-k3AMEr-run-graph-canvas/`.
4. **Perf evidence.** Record the measured `build_run_graph` (200/500) and `computeLayout` (200/500)
   timings from the T-M4qboy and T-adVpTj tests, plus a manual Chrome DevTools performance trace of
   pan/zoom on the 180-node run (fps estimate), in STATUS.

## Acceptance Criteria
1. E2E (a) passes. The asserted spawn edges equal
   `{emitter→each emitted, gate→each iter-2 clone}`, and the asserted dependency edges equal the
   workflow's declared plus loop-resolved edges. `spawn_edges != dependency_edges` (as sets of pairs).
2. E2E (b) passes. `spec_sessions` has length 1 and `workflow.snapshot.<sha12>.json` exists.
3. The browser smoke passes locally (`/usr/bin/google-chrome` via Playwright `channel="chrome"`).
   The node count equals the expected value, the spawn-view edge count equals the expected value,
   the panel opens, there are **zero** CSP violations, console errors, and page errors, **and** the
   negative control records ≥ 1 violation. The pytest output is pasted into STATUS.
4. Screenshots exist under `output/E-k3AMEr-run-graph-canvas/` and are linked from STATUS.
5. Perf: `build_run_graph` ≤ 150 ms, `computeLayout` ≤ 300 ms, and pan/zoom visually ≥ 30 fps on
   the dev machine, **all recorded**. If any exceeds its target, it is marked as a failing gate and
   raised to dev-epic (never silently accepted).
6. Resolve the OPEN_QUESTION: record the smoke's runtime and a recommendation (CI vs opt-in) in
   STATUS. If recommending CI, include the exact `ci.yml` step. The user or dev-epic decides.
7. The full suites are green: `pytest -q`, the UI coverage gate, `ruff`, `mypy src`, and in `ui/`:
   typecheck, test, build, and `npm audit --omit=dev --audit-level=high`.

## Risks
- Headless timing flakiness. Use Playwright auto-waiting (`wait_for_selector` with a named timeout
  constant), never fixed sleeps.
- A new optional test dependency (`playwright`). It is confined to the `browser` extra and the
  `browser` marker, and is never imported by production code.

## Dependencies
- `T-AsQ77e`, `T-OjTS8O`, `T-aHktGB`, `T-pAi0Cv`.

## Pseudocode / Algorithm
```text
INIT_JS = "window.__cspViolations=[];document.addEventListener('securitypolicyviolation',e=>window.__cspViolations.push(e.violatedDirective))"
test_browser_smoke(page): page.add_init_script(INIT_JS); errors=[]; page.on("console", c -> errors.append if c.type=="error"); page.on("pageerror", errors.append)
  page.goto(base); page.click(run_row); page.click("role=tab[name=Graph]"); page.wait_for_selector(".react-flow__node")
  assert count(.react-flow__node)==N; toggle spawn; assert count(.react-flow__edge)==E_spawn; click node; assert panel label
  assert page.evaluate("window.__cspViolations")==[] and errors==[]; screenshots
test_browser_negative_control(page): serve page with SPA_CSP header + inline <script>; assert page.evaluate("window.__cspViolations") != []
```

## Schemas / Interface Notes
- Interface: `write_synthetic_run(root: Path, waves: int, fanout: int, *, clock) -> str (run_id)`.
- Spec / data schema: none new. Triggers / events: N/A.
- Artifacts: `output/E-k3AMEr-run-graph-canvas/*.png`.

## Handoff Boundary
- Upstream: all MVP implementation tasks.
- Downstream: Gate G2 (dev-security reviews the evidence), then `T-oroE5f`.

## Artifacts
- Docs/comments: `meta/tickets/E-k3AMEr-run-graph-canvas/T-F1caAt-graph-e2e-verification/`
- Large outputs: `output/E-k3AMEr-run-graph-canvas/`
