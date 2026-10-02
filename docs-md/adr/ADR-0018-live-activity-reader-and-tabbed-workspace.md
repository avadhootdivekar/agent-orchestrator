# ADR-0018: Read-only live-activity reader over transcripts, and a router-less tabbed dashboard workspace

- Status: Accepted (2026-10-02). Epic `E-iafh2F-live-status-and-tabbed-workspace`.
- Related: ADR-0010, ADR-0011, ADR-0017. Design: `docs-md/live-activity-and-tabs-hld.md`.

## Context
Running tasks show 0 tokens (usage mirrored at settle) and no turn count anywhere; the dashboard
is a single view that is replaced when a run is opened.

## D1 — Derive live activity at read time from `transcript.jsonl`; no engine/state change
Alternatives: (a) engine writes live counters into `state.json` (hot-path writes, schema change,
resume interplay, parallel-task contention); (b) tail inside the client (no server file access,
unbounded). Chosen: bounded, cached, incremental server-side tail in `ui/activity.py`, pure
builder + I/O split. Trade-off: counts are best-effort (`approximate` flag on huge/bursty files),
cost is a floor (finished attempts only) because no price table exists.

## D2 — Separate `/api/runs/{id}/activity` endpoint
Keeps run detail a pure function of `state.json`; independent degradation; one place for bounds.

## D3 — Fixed 3-row, non-collapsible "Now running" box
User requirement: consistent, non-heterogeneous behaviour. Overflow scrolls inside the box.

## D4 — Tabs without a router library; hash is the URL contract
Hash-only routes need no backend change (SPA fallback exists) and survive static hosting. Tabs
are a closed `kind` allowlist; hash and localStorage are untrusted input validated per kind
(ADR-0011 stance). Inactive tabs stay mounted (state preserved) but pause polling.
Split-pane is explicitly deferred.

## Consequences
Activity numbers can lag one poll and are lower bounds under extreme output; tab params are
display-only inputs re-validated server-side; adding a tab kind means extending the allowlist,
codec and tests together.
