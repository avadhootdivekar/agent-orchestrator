# Dashboard frontend

React + TypeScript + Vite. Served by `ao ui` — see the
[Dashboard section of the root README](../README.md#dashboard-ao-ui) for the user-facing
docs and [the HLD](../docs-md/dashboard-and-general-instructions-hld.md) for the design.

## Build output is committed — on purpose

`npm run build` writes to **`../src/agent_orchestrator/ui/static/`**, inside the Python
package. That directory is committed so `pip install` ships a working dashboard without
requiring node at install time.

**If you change anything under `src/`, re-run `make ui-build` and commit the result.**
`node_modules/` is git-ignored; the build output is not.

## Commands

Run these from the repo root (they wrap the npm scripts here):

```bash
make ui-install     # npm install
make ui-build       # build into the Python package
make ui-test        # vitest
make ui-typecheck   # tsc -b --noEmit
make ui             # build, then serve via `ao ui`
make ui-dev         # vite dev server on :5173, proxying /api to :8765
```

For hot reload: run `make ui` in one terminal (serves the API on `:8765`), then `make ui-dev`
in another and open `:5173`. The dev server proxies `/api` across, so the frontend reloads on
save while talking to a real backend.

## Layout

```
src/
  main.tsx            entry point
  App.tsx             shell: sidebar nav, view switching, theme toggle
  api.ts              typed client for /api (throws ApiError carrying the status code)
  types.ts            response shapes, mirroring src/agent_orchestrator/ui/app.py
  format.ts           pure display helpers — bytes, durations, cost, status tone/glyph
  styles.css          theme tokens + layout; light and dark both explicitly defined
  components/
    common.tsx        StatusChip, Tile, ErrorBanner, LiveBadge, Empty
    RunsList.tsx      run table + workspace-wide stat tiles
    RunDetail.tsx     per-run stats, task table, tripped breakers, CLI log
    NewRun.tsx        workflow picker + prompt box + override fields
    FileBrowser.tsx   directory tree + code viewer
    Settings.tsx      workspace config + effective general instructions
  test/               vitest suites (jsdom, mocked fetch)
```

## Conventions

- **No runtime dependencies beyond React.** Everything else is a devDependency. Keeping the
  bundle self-contained is what lets the dashboard work offline and ship inside a wheel.
- **Status is never color-alone.** `StatusChip` renders a glyph *and* the status word;
  color reinforces. This keeps the UI readable in grayscale, under `forced-colors`, and for
  colorblind users.
- **Colors come from theme tokens in `styles.css`**, not hardcoded hex in components. Both
  light and dark are explicitly stepped (from the project's validated data-viz palette) —
  dark is a chosen set of values, not an automatic inversion. The OS setting and the in-app
  toggle are both honoured, and the toggle wins in either direction.
- **Tabular figures only in aligned columns** (`.num` in tables). Large standalone values —
  stat tiles, the hero figure — use proportional figures, which read better at display size.
- **One hero figure per view.**
- **Wide content scrolls inside its own container** (`.table-wrap`); the page body never
  scrolls horizontally.
- **`format.ts` stays pure** so it is directly unit-testable; components hold the I/O.
