# ADR-0006 — Per-agent declarative config over run-level CLI overrides for agent policy

- Status: **Accepted** (2026-07-15)
- Date: 2026-07-15
- Deciders: Avadhoot Divekar (user), Claude (developer role)
- Related: ADR-0003 (settings precedence — the runtime composition rule this builds on) · ADR-0005 (headless tool policy — first knob decided under this guideline) · memory `project_model_override_clobbers_agents` · `src/agent_orchestrator/models.py::AgentSpec`

## Context

The framework keeps growing per-agent knobs on `AgentSpec` — `model`, `effort`, `max_turns`,
`working_dir`, and now `disallowed_tools` (ADR-0005). Each addition raises the same recurring question:
should the knob *also* get a global `ao run --X` invocation flag that applies to every agent at once?

ADR-0003 already resolved how invocation-level settings **compose** with per-agent ones (more-specific
wins; invocation values are fill-in defaults, not clobbers) and documented the live defect where a
global `--model` silently overwrites deliberately-pinned per-agent models. This ADR generalizes the
**design-time** question that comes *before* that composition rule: for a new per-agent knob, do we add
a global CLI flag at all?

## Decision

**The default home for agent policy is a declarative `AgentSpec` field (+ `agents.schema.json`), not a
run-level CLI flag.** A global/invocation-level flag is added only when **both** hold:

1. There is a genuine whole-run use case — a setting the operator legitimately wants to sweep across
   every agent for a single invocation (e.g. "rerun everything cheap on haiku").
2. It obeys ADR-0003: it is a *fill-in default* (applies only where no agent/workflow/task value is
   declared), or an explicitly-named loud override (`--force-*`). **Never a silent global clobber** of
   per-agent intent.

Corollaries:

- Per-agent-*varying* policy (tool selection, working directory) → per-agent field **only**. A global
  flag here is precedence inversion — the generic value clobbering the specific one, exactly the
  ADR-0003 `--model` defect.
- `extra_args` already provides a per-agent, explicit CLI escape hatch for provider flags, so an ad-hoc
  "just pass this flag to one agent" need never justifies a new global flag.

### Applied to tool policy (ADR-0005)

`disallowed_tools` is a per-agent field with no `ao run --disallow-tools` counterpart: a research agent
wanting web and a codegen agent silencing background tools is the norm, not a run-wide sweep, and
`extra_args` covers the explicit-CLI case.

## Consequences

- New per-agent knobs get a schema-validated field (and, when relevant, a documented default); the "do
  we also need a CLI flag?" question has a standing answer instead of being re-litigated per feature.
- A future legitimate whole-run knob is still allowed — but must be explicit and precedence-correct per
  ADR-0003, not a silent clobber.

## Alternatives considered

- **A global CLI flag for every settable knob** — rejected: reproduces the `--model` clobber footgun,
  is the wrong granularity for per-agent-varying policy, and grows CLI surface for cases that
  `extra_args` / per-agent fields already serve.
- **Fold this into ADR-0003** — ADR-0003 governs *runtime precedence* among an enumerated settings set;
  this is the *design-time* gate for whether a *new* knob becomes global at all. Kept separate and
  cross-linked to avoid overloading ADR-0003.
