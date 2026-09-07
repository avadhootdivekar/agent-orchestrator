"""Load and validate workflow spec files."""

from __future__ import annotations

import os
import posixpath
import re
import subprocess
from collections import defaultdict
from itertools import product
from pathlib import Path, PurePosixPath

from .breakers import BREAKER_REGISTRY
from .config import _load_file, _validate_against_schema
from .dag import Graph, compute_cones, forward_closure
from .errors import SpecValidationError
from .models import (
    ISOLATION_WORKTREE,
    TIER_LLM,
    BudgetSpec,
    IntegrationSpec,
    TaskSpec,
    WorkflowSpec,
    _declared_isolation,
    _is_structural_task,
    resolve_task_isolation,
)

# Suffix used for loop iteration cloning — authors must not use this in task ids.
_ITER_SUFFIX_MARKER = "__iter"

# E-Wk9Tz3 V9: a task id that sanitizes to this reserved component collides with the
# integration branch ao/<run_id>/integration. Task ids are already pattern-constrained to
# lowercase by specs/workflow.schema.json, but that schema is NOT packaged into an
# installed `ao` wheel (see cross_validate's packaging note below) -- lowercasing here is
# the one normalization needed to also catch a mixed-case id when that gate is absent.
_RESERVED_INTEGRATION_COMPONENT = "integration"

# Bound on the `git rev-parse` probes V8 performs so `ao validate` can never hang on a
# slow/blocked filesystem (safe-by-default) -- named, not a magic literal.
_GIT_PROBE_TIMEOUT_SECONDS = 5

# Router/route/breaker id pattern (mirrors specs/workflow.schema.json's task/router/
# circuitBreaker id patterns). JSON Schema already enforces this for `router.id` and
# `circuit_breakers[].id` at load time, but NOT for route ids (dict keys of
# `router.routes`, which the schema leaves unconstrained) — validate_run_control
# checks all three so route ids get the same guarantee (rule 11).
_ROUTING_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-_]*$")

# The implemented circuit-breaker conditions. The schema's `condition` enum accepts the
# full HLD §6 catalog so specs may reference not-yet-implemented conditions ahead of
# time; this validator rejects those with a clean, named error rather than letting them
# reach the engine as a silent no-op. Derived from BREAKER_REGISTRY (the engine's actual
# implementations) instead of a hand-maintained copy: a previous hardcoded set here
# silently drifted when E-3JTmVu added `run_active_seconds` to the registry but not to
# this list, making `ao validate` reject a fully-implemented condition.
_MVP_BREAKER_CONDITIONS = frozenset(BREAKER_REGISTRY)

# Safety cap on the route-selection combinations enumerated for rule 7's any-join
# satisfiability check (product of route counts across all routers). Real specs have
# a handful of routers/routes; this guards against a pathological spec turning
# `ao validate` into a combinatorial hang (NFR "safe by default").
_MAX_ANY_JOIN_COMBINATIONS = 4096


def load_workflow(path: str | Path) -> WorkflowSpec:
    """Load a workflow JSON/YAML file, validate against JSON Schema, parse into WorkflowSpec."""
    data = _load_file(path)
    _validate_against_schema(data, "workflow.schema.json")
    try:
        return WorkflowSpec(**data)
    except Exception as exc:
        raise SpecValidationError(f"Workflow model error: {exc}", path=str(path)) from exc


def cross_validate(
    workflow: WorkflowSpec,
    reposets: dict,
    agents: dict,
) -> list[str]:
    """Cross-validate workflow references against loaded reposets and agents.

    Raises SpecValidationError for:
    - Unknown repo_set
    - Task referencing an unknown agent
    - Task depends_on referencing an unknown task id or an unknown loop id not resolvable
    - emit_tasks / task_manifest_path pairing violations (Area 2)
    - LoopSpec body/gate/max_iterations constraints (Area 2)
    - Authored task ids or loop body ids containing '__iter' (reserved suffix, ADR-006)

    Returns a list of non-fatal WARNING strings (C-3): currently only from the isolation/
    integration/scheduling rules V4/V5/V7/V10 (`_cross_validate_isolation`) -- every other
    rule in this function is fatal-only and raises `SpecValidationError` as before. Callers
    that don't care about warnings (nearly every existing call site/test) can simply ignore
    the return value, exactly as they always have; `cli._load_all` prints them the same way
    it already prints `validate_run_control`'s own warnings (`WARNING: <text>`).
    """
    if workflow.repo_set not in reposets:
        raise SpecValidationError(
            f"Unknown repo_set: {workflow.repo_set!r}",
            path="repo_set",
        )

    task_ids = {t.id for t in workflow.tasks}
    loop_ids = {lp.id for lp in workflow.loops}

    # Build set of valid dep targets: task ids + loop ids (loop id resolves to last iter last task)
    valid_dep_targets = task_ids | loop_ids

    # Check for reserved suffix in authored task ids
    for task in workflow.tasks:
        if _ITER_SUFFIX_MARKER in task.id:
            raise SpecValidationError(
                f"Task id {task.id!r} contains reserved suffix {_ITER_SUFFIX_MARKER!r}; "
                "authored ids must not use __iter",
                path=f"tasks.{task.id}.id",
            )

    for task in workflow.tasks:
        if task.agent not in agents:
            raise SpecValidationError(
                f"Task {task.id!r}: unknown agent {task.agent!r}",
                path=f"tasks.{task.id}.agent",
            )
        for dep in task.depends_on:
            if dep not in valid_dep_targets:
                raise SpecValidationError(
                    f"Task {task.id!r}: unknown depends_on {dep!r}",
                    path=f"tasks.{task.id}.depends_on",
                )

        # emit_tasks ⇔ task_manifest_path both set or both unset (§3.1 cross-validation)
        if task.emit_tasks and not task.task_manifest_path:
            raise SpecValidationError(
                f"Task {task.id!r}: emit_tasks=True requires task_manifest_path to be set",
                path=f"tasks.{task.id}.emit_tasks",
            )
        if task.task_manifest_path and not task.emit_tasks:
            raise SpecValidationError(
                f"Task {task.id!r}: task_manifest_path is set but emit_tasks=False",
                path=f"tasks.{task.id}.task_manifest_path",
            )

    # LoopSpec cross-validation (§3.2)
    body_task_to_loop: dict[str, str] = {}  # task_id -> loop_id
    for loop in workflow.loops:
        if not loop.body:
            raise SpecValidationError(
                f"Loop {loop.id!r}: body must not be empty",
                path=f"loops.{loop.id}.body",
            )

        if loop.max_iterations < 1:
            raise SpecValidationError(
                f"Loop {loop.id!r}: max_iterations must be >= 1, got {loop.max_iterations}",
                path=f"loops.{loop.id}.max_iterations",
            )

        for bid in loop.body:
            if bid not in task_ids:
                raise SpecValidationError(
                    f"Loop {loop.id!r}: body task {bid!r} not found in workflow tasks",
                    path=f"loops.{loop.id}.body",
                )
            if _ITER_SUFFIX_MARKER in bid:
                raise SpecValidationError(
                    f"Loop {loop.id!r}: body task id {bid!r} contains reserved suffix "
                    f"{_ITER_SUFFIX_MARKER!r}",
                    path=f"loops.{loop.id}.body",
                )
            if bid in body_task_to_loop:
                raise SpecValidationError(
                    f"Loop {loop.id!r}: task {bid!r} already belongs to loop "
                    f"{body_task_to_loop[bid]!r}; a task may be in at most one loop body",
                    path=f"loops.{loop.id}.body",
                )
            body_task_to_loop[bid] = loop.id

            # No nested loops: body task id must not equal any loop id
            if bid in loop_ids:
                raise SpecValidationError(
                    f"Loop {loop.id!r}: body task id {bid!r} matches a loop id (nested loops "
                    "are not supported in this release)",
                    path=f"loops.{loop.id}.body",
                )

        if loop.gate_task_id not in loop.body:
            raise SpecValidationError(
                f"Loop {loop.id!r}: gate_task_id {loop.gate_task_id!r} not in body",
                path=f"loops.{loop.id}.gate_task_id",
            )

        # Gate task must not itself be an emitter (a task cannot be both emitter and gate)
        gate_task = next(t for t in workflow.tasks if t.id == loop.gate_task_id)
        if gate_task.emit_tasks:
            raise SpecValidationError(
                f"Loop {loop.id!r}: gate_task_id {loop.gate_task_id!r} has emit_tasks=True; "
                "a task cannot be both an emitter and a gate",
                path=f"loops.{loop.id}.gate_task_id",
            )

    # Budget cross-validation (T-oh5gl5)
    if workflow.budget is not None:
        budget_cross_validate(workflow.budget)

    # Per-task git isolation & rebase-integration cross-validation (E-Wk9Tz3, HLD §10.4,
    # rules V1-V12). Packaging note (pre-existing, inherited): specs/*.schema.json is not
    # packaged into the wheel and config._validate_against_schema silently no-ops when the
    # file is absent -- so for an installed `ao`, THIS function is the real gate. Every
    # rule below must therefore exist here, not only in JSON Schema.
    return _cross_validate_isolation(workflow, reposets, agents)


def budget_cross_validate(budget: BudgetSpec) -> None:
    """Standalone budget cross-validation (called from CLI and cross_validate)."""
    b = budget
    if b.total_tokens is not None and b.total_tokens <= 0:
        raise SpecValidationError(
            "budget.total_tokens must be > 0",
            path="budget.total_tokens",
        )
    if b.rate is not None:
        has_window = b.rate.window is not None
        has_secs = b.rate.window_seconds is not None
        if has_window == has_secs:  # both set or neither set
            raise SpecValidationError(
                "budget.rate: set exactly one of window or window_seconds",
                path="budget.rate",
            )
        if b.rate.tokens <= 0:
            raise SpecValidationError(
                "budget.rate.tokens must be > 0",
                path="budget.rate.tokens",
            )
    if b.estimator.pessimism_buffer < 1.0:
        raise SpecValidationError(
            "budget.estimator.pessimism_buffer must be >= 1.0",
            path="budget.estimator.pessimism_buffer",
        )


# ---------------------------------------------------------------------------
# Per-task git isolation & rebase-integration cross-validation (E-Wk9Tz3, HLD §10.4).
# ---------------------------------------------------------------------------


def _is_unsafe_relative_glob(value: str) -> bool:
    """True if *value* is an absolute path or escapes its base via a '..' segment.

    Shared by V6 (``touches``) and V12 (``commit_denylist`` / ``touches`` /
    ``resolvers.union``) -- every glob in this epic's schema is documented as workspace/
    worktree-RELATIVE (HLD §10.1), so either shape can only be a mistake or an attempt to
    point outside the isolated worktree. Checked with POSIX-only semantics (`posixpath`,
    not `os.path`) so the result is the same regardless of the HOST platform `ao validate`
    happens to run on (C-6: `os.path.isabs` uses the host's own rules -- on Linux it does
    not recognize a Windows-style absolute path at all, which would make the same glob
    string pass or fail this check depending on where `ao validate` runs; `posixpath.isabs`
    is platform-independent by construction, matching this function's own "always
    posix-style" documented contract).
    """
    if not value:
        return False
    if posixpath.isabs(value):
        return True
    return ".." in PurePosixPath(value).parts


def _git_rev_parse(path: str, *args: str) -> str | None:
    """Best-effort ``git rev-parse <args>`` probe for V8, resolved at *path*.

    Returns None (skip the check) for anything that isn't a real, on-disk git checkout --
    "git" not installed, *path* not a git repo, or a probe that times out -- so V8 can only
    ever fire a false POSITIVE, never a false negative that would block an otherwise-valid
    non-git workspace.

    Deliberately a small, self-contained subprocess call rather than a dependency on
    `isolation/git.py` (T-Gt4Pw8, developed concurrently under this same epic): this rule
    only needs a yes/no toplevel + common-dir probe at VALIDATE time, not that module's
    full typed porcelain (which exists to serve WORKTREE/INTEGRATION operations at run
    time) -- pulling in a hard dependency on a module still under active, independent
    development would couple two tickets that this epic deliberately kept on disjoint file
    sets. `isolation/git.py`'s `GitRepo._run` remains the epic's single HARDENED choke
    point (S-1: it also suppresses git hooks via a computed empty hooks dir, which needs
    `isolation/paths.py` machinery this probe has no reason to depend on); this function
    replicates only the CHEAP, dependency-free part of that hardening locally (C-4):
    `GIT_TERMINAL_PROMPT=0` so a probe against a misconfigured remote can never block on an
    interactive credential/host-key prompt, and `LC_ALL=C` for locale-independent,
    reliably-parseable output. Bounded by `timeout=` regardless, so a stuck child is always
    forcibly killed even without these -- this is defense-in-depth/consistency with the
    repo's established git-safety posture, not the only thing standing between this probe
    and a hang.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", *args],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=_GIT_PROBE_TIMEOUT_SECONDS,
            check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    output = result.stdout.strip()
    return output or None


def _check_no_foreign_worktree_collisions(workflow: WorkflowSpec, reposets: dict) -> None:
    """V8: fatal when two ``RepoRef``s in this workflow's reposet resolve into the SAME
    underlying git repository (identical ``--git-common-dir``) via DIFFERENT toplevels.

    That signature can only mean one of the two paths is a pre-existing, on-disk LINKED git
    worktree of the other's repository (R-14: clarified wording -- this rule is about
    foreign checkouts found on disk at validate time via git probing, never about ao's own
    not-yet-created worktrees, which don't exist until dispatch). A plain nested
    subdirectory of the SAME repo (e.g. a reposet's ``docs`` member living under its
    ``core`` member's toplevel, HLD §7.1) shares one toplevel and is explicitly NOT a
    violation -- that is ordinary same-repo grouping, not a worktree collision.

    Only called when this workflow actually resolves at least one task to isolation=
    "worktree" (a pre-existing foreign-worktree configuration poses no danger, and probing
    the filesystem would only slow down every non-isolated `ao validate` for no benefit).
    """
    reposet = reposets.get(workflow.repo_set)
    if reposet is None:
        return  # unknown repo_set is reported by cross_validate's own rule, not here

    probes: list[tuple[str, str, str]] = []  # (repo_id, toplevel, common_dir)
    for repo in reposet.repos:
        toplevel = _git_rev_parse(repo.path, "--show-toplevel")
        if toplevel is None:
            continue
        common_dir = _git_rev_parse(repo.path, "--git-common-dir")
        if common_dir is None:
            continue
        # --git-common-dir may be printed relative to cwd; resolve for a stable comparison.
        common_dir = str(Path(repo.path, common_dir).resolve())
        probes.append((repo.id, toplevel, common_dir))

    for i, (repo_id_a, toplevel_a, common_dir_a) in enumerate(probes):
        for repo_id_b, toplevel_b, common_dir_b in probes[i + 1 :]:
            if toplevel_a == toplevel_b:
                continue  # same repo, ordinary nested-subdirectory grouping (§7.1) -- fine
            if common_dir_a == common_dir_b:
                raise SpecValidationError(
                    f"RepoRefs {repo_id_a!r} and {repo_id_b!r} resolve into the same git "
                    f"repository (common dir {common_dir_a!r}) via different toplevels "
                    f"({toplevel_a!r} vs {toplevel_b!r}) -- one is a pre-existing, on-disk "
                    "git worktree of the other; reposets must point at independent "
                    "checkouts",
                    path=f"repo_set.{workflow.repo_set}",
                )


def _any_task_isolated(workflow: WorkflowSpec, tasks: list[TaskSpec]) -> bool:
    """True if isolation is "active" for *workflow* given *tasks* (C-2).

    Two ways this can be true: `workflow.defaults.isolation == "worktree"` (statically
    known regardless of which tasks exist -- ANY inherit-defaulted task, INCLUDING one not
    yet present, such as an `emit_tasks`-injected child, will isolate under it), or at
    least one of *tasks* resolves (via `resolve_task_isolation`) to isolation="worktree"
    directly.

    C-2 fix: an earlier version of this gate checked only the second condition, which
    missed a workflow whose only STATIC tasks are structural (forced to isolation="none"
    by `resolve_task_isolation`) but whose `defaults.isolation="worktree"` isolates every
    `emit_tasks`-injected child at runtime -- `workflow.defaults.isolation` costs nothing
    to check directly and closes that gap for every caller of this function.
    """
    if workflow.defaults.isolation == ISOLATION_WORKTREE:
        return True
    return any(resolve_task_isolation(t, workflow) == ISOLATION_WORKTREE for t in tasks)


def validate_isolation(workflow: WorkflowSpec, tasks: list[TaskSpec]) -> list[str]:
    """Cross-validation rules V1, V2, V4, V5, V6, V7, V9, V11, V12 (HLD §10.4) -- every
    isolation/integration/scheduling rule that needs NO external registry (reposet/agent
    lookups). V3 (resolver_agent existence), V8 (foreign-worktree collision) and V10
    (resolver agent's disallowed_tools coverage) DO need the `agents`/`reposets`
    registries and are therefore NOT part of this function; `_cross_validate_isolation`
    calls this function first and then adds those three.

    Fatal rules raise `SpecValidationError` (naming the offending task id/field, per
    CLAUDE.md's "actionable error messages" rule) exactly as before. Warning rules
    (V4/V5/V7) are RETURNED, not logged (C-3): `ao validate` attaches no log handler for
    the package logger (only `ao run`/`ao resume` do, via `attach_run_handler`), so a bare
    `logger.warning` call there is invisible -- printed by Python's "handler of last
    resort" with no `WARNING:` label, easy to mistake for stray output or miss entirely
    when grepping stderr. The caller prints these the same way `cli._load_all` already
    prints `validate_run_control`'s own warnings: ``WARNING: <text>``.

    HOOK POINT for `T-En8Hd4` (documented in this ticket's STATUS.md): *tasks* is accepted
    SEPARATELY from `workflow.tasks` precisely so a caller can validate a DIFFERENT (e.g.
    dynamically expanded) task list without mutating `workflow` itself. Call
    ``validate_isolation(workflow, workflow.tasks + newly_injected_tasks)`` at
    `emit_tasks` injection time so an isolation misconfiguration that only manifests
    through an emit_tasks-declared ``isolation: "worktree"`` (independent of
    `workflow.defaults.isolation`) is caught the moment the manifest is read, not
    silently deferred to the first isolated dispatch mid-run (C-2's second, harder gap --
    accepted as a documented limitation of the STATIC `ao validate` pass alone, closed
    only when a caller actually re-validates at injection time).

    V1/V2/V7/V11 all inspect `integration.*` fields whose ONLY effect is on an isolated
    task's landing -- and `IntegrationSpec.ladder`'s own default already includes "llm"
    (HLD §10.2), so evaluating those rules unconditionally would fatal on `resolver_agent`
    being unset for EVERY workflow that never isolates a single task, which would break
    NFR-2/NFR-5 backward compatibility outright. They are therefore only evaluated when
    `_any_task_isolated` is true; when it is not, V5 alone warns that the block has no
    effect. V4/V6/V9/V12 are task-level/id-level/glob-hygiene checks that apply regardless
    of whether integration ever engages, so they run unconditionally first.
    """
    integ = workflow.integration
    warnings: list[str] = []

    # V4: a structural task (emit_tasks/router/loop-gate) whose declared isolation
    # (direct or inherited from defaults.isolation) would be "worktree" has no effect.
    # This is the validate-time counterpart of resolve_task_isolation's own runtime
    # warning -- both share `_is_structural_task`/`_declared_isolation` so they can never
    # independently drift on what counts as "structural".
    for task in tasks:
        if (
            _is_structural_task(task, workflow)
            and _declared_isolation(task, workflow) == ISOLATION_WORKTREE
        ):
            warnings.append(
                f"Task {task.id!r}: isolation={ISOLATION_WORKTREE!r} (declared or "
                "inherited from defaults.isolation) has no effect -- it is an "
                "emit_tasks/router/loop-gate task, always forced to isolation='none' at "
                "dispatch"
            )

    # V6/V12: touches / commit_denylist / resolvers.union globs must be workspace/
    # worktree-relative (no absolute path, no '..' segment).
    for task in tasks:
        for glob in task.touches:
            if _is_unsafe_relative_glob(glob):
                raise SpecValidationError(
                    f"Task {task.id!r}: touches entry {glob!r} must be workspace-relative "
                    "(no absolute path, no '..' segment)",
                    path=f"tasks.{task.id}.touches",
                )
    for glob in integ.commit_denylist:
        if _is_unsafe_relative_glob(glob):
            raise SpecValidationError(
                f"integration.commit_denylist entry {glob!r} must be workspace/worktree-"
                "relative (no absolute path, no '..' segment)",
                path="integration.commit_denylist",
            )
    for glob in integ.resolvers.union:
        if _is_unsafe_relative_glob(glob):
            raise SpecValidationError(
                f"integration.resolvers.union entry {glob!r} must be workspace/worktree-"
                "relative (no absolute path, no '..' segment)",
                path="integration.resolvers.union",
            )

    # V9: a task id that sanitizes to the reserved component "integration".
    for task in tasks:
        if task.id.lower() == _RESERVED_INTEGRATION_COMPONENT:
            raise SpecValidationError(
                f"Task id {task.id!r} sanitizes to the reserved component "
                f"{_RESERVED_INTEGRATION_COMPONENT!r}, which collides with the "
                "integration branch ao/<run_id>/integration",
                path=f"tasks.{task.id}.id",
            )

    # V5: integration.* configured but no task resolves to worktree isolation -- warn, and
    # skip every remaining rule below: an inactive integration block cannot violate
    # anything it never governs.
    if not _any_task_isolated(workflow, tasks):
        if integ != IntegrationSpec():
            warnings.append(
                "workflow.integration is configured but no task resolves to isolation="
                "'worktree'; integration config has no effect"
            )
        return warnings

    llm_in_ladder = TIER_LLM in integ.ladder

    # V1: strategy == "merge" is reserved, not implemented in this version.
    if integ.strategy == "merge":
        raise SpecValidationError(
            "integration.strategy='merge' is reserved and not implemented in this "
            "version; only 'rebase' is supported",
            path="integration.strategy",
        )

    # V2: "llm" in ladder requires resolver_agent to be set.
    if llm_in_ladder and integ.resolver_agent is None:
        raise SpecValidationError(
            "integration.ladder includes 'llm' but integration.resolver_agent is not set",
            path="integration.resolver_agent",
        )

    # V7: max_resolver_attempts > 0 while "llm" absent from ladder -- dead config.
    if integ.max_resolver_attempts > 0 and not llm_in_ladder:
        warnings.append(
            f"integration.max_resolver_attempts={integ.max_resolver_attempts} has no "
            f"effect: 'llm' is not in integration.ladder {integ.ladder!r}"
        )

    # V11: an explicit opt-out of the resolver's tool-egress guard is fatal -- handing raw,
    # unreviewed conflict content to an agent with unrestricted egress must never be a
    # silent default (this version offers no deliberate opt-out mechanism).
    if llm_in_ladder and integ.resolver_disallowed_tools == []:
        raise SpecValidationError(
            "integration.resolver_disallowed_tools=[] while 'llm' is in "
            "integration.ladder; an empty list hands the resolver unrestricted tool "
            "egress over raw, unreviewed conflict content",
            path="integration.resolver_disallowed_tools",
        )

    return warnings


def _cross_validate_isolation(
    workflow: WorkflowSpec,
    reposets: dict,
    agents: dict,
) -> list[str]:
    """Full isolation/integration/scheduling validation (HLD §10.4, V1-V12): calls
    `validate_isolation` for the registry-independent rules, then adds the three that DO
    need `agents`/`reposets` -- V3 (resolver_agent existence), V8 (foreign-worktree
    collision), V10 (resolver agent's disallowed_tools coverage). Returns the combined
    warning list; fatal rules raise `SpecValidationError` as always.
    """
    warnings = validate_isolation(workflow, workflow.tasks)

    if not _any_task_isolated(workflow, workflow.tasks):
        # V3/V8/V10 all govern an isolated task's landing; nothing left to check when
        # isolation never engages (mirrors validate_isolation's own V5 early-return).
        return warnings

    integ = workflow.integration
    llm_in_ladder = TIER_LLM in integ.ladder

    # V3: resolver_agent, when set, must be a known agent (checked regardless of whether
    # "llm" is currently in the ladder -- a bogus agent name is always a mistake).
    resolver_agent_spec = None
    if integ.resolver_agent is not None:
        resolver_agent_spec = agents.get(integ.resolver_agent)
        if resolver_agent_spec is None:
            raise SpecValidationError(
                f"integration.resolver_agent {integ.resolver_agent!r} is not a known agent",
                path="integration.resolver_agent",
            )

    # V8: a RepoRef path that lies inside a pre-existing, on-disk git worktree of another
    # reposet member.
    _check_no_foreign_worktree_collisions(workflow, reposets)

    # V10: the named agent's own disallowed_tools should already cover the force-injected
    # set -- informational only; the dispatch injects the union regardless (S-2), so this
    # is a "your spec is misleading" notice, not a hole.
    if llm_in_ladder and resolver_agent_spec is not None:
        agent_disallowed = set(resolver_agent_spec.disallowed_tools)
        missing = sorted(set(integ.resolver_disallowed_tools) - agent_disallowed)
        if missing:
            warnings.append(
                f"integration.resolver_agent {integ.resolver_agent!r}'s own "
                f"disallowed_tools does not already include {missing} (force-injected "
                "onto the resolver dispatch regardless, per S-2)"
            )

    return warnings


# ---------------------------------------------------------------------------
# Run-control (routing + circuit breakers) static validation — LLD §4.4,
# epic E-rc7k2v, T-w6p2c8. Runs AFTER `build_dag` succeeds (needs the runtime
# graph, declared + inferred edges, for reachability/cone computation).
# ---------------------------------------------------------------------------


def _check_reserved_id(value: str, path: str) -> None:
    """Rule 11: router/route/breaker ids must match the id pattern and must not
    contain the reserved loop-iteration marker.

    JSON Schema already enforces the pattern for `router.id` and
    `circuit_breakers[].id` at load time; this is defense-in-depth for those two
    plus the ONLY enforcement point for route ids (dict keys of `router.routes`,
    which the schema leaves unconstrained).
    """
    if not _ROUTING_ID_PATTERN.match(value):
        raise SpecValidationError(
            f"{path}: id {value!r} must match pattern '^[a-z0-9][a-z0-9-_]*$'",
            path=path,
        )
    if _ITER_SUFFIX_MARKER in value:
        raise SpecValidationError(
            f"{path}: id {value!r} contains reserved suffix {_ITER_SUFFIX_MARKER!r}",
            path=path,
        )


def _effective_deps(task: TaskSpec, graph: Graph) -> set[str]:
    """A task's declared depends_on plus any inferred producer of its declared
    inputs (LLD §4.4 rule 5/7's "effective = depends_on ∪ inferred-producer")."""
    deps = set(task.depends_on)
    for inp in task.inputs:
        producer = graph.producer_of(inp)
        if producer is not None:
            deps.add(producer)
    return deps


def _simulate_route_selection(
    tasks_by_id: dict[str, TaskSpec],
    graph: Graph,
    cones: dict[str, dict[str, set[str]]],
    membership: dict[str, set[tuple[str, str]]],
    order: list[str],
    selection: dict[str, str],
) -> dict[str, bool]:
    """Pure static simulation of not_taken propagation for ONE route selection
    (router_id -> selected route_id, one per router).

    Mirrors the engine's runtime activation rules (LLD §5.2/§5.4) at validation
    time: a task is inactive if it sits in an exclusive cone whose route was NOT
    selected; otherwise it follows `join` semantics over its effective
    dependencies (all => every dep active; any => at least one dep active).
    No I/O, no clock — deterministic given (tasks, graph, cones, membership,
    selection). Used only by rule 7 (any-join satisfiability).
    """
    active: dict[str, bool] = {}
    for tid in order:
        task = tasks_by_id.get(tid)
        if task is None:
            # Not an authored task (e.g. a resolved loop-iteration clone id) —
            # outside routing's static concern; treat as non-blocking.
            active[tid] = True
            continue

        forced_inactive = False
        for router_id, route_id in membership.get(tid, ()):
            if (
                tid in cones.get(router_id, {}).get(route_id, ())
                and selection.get(router_id) != route_id
            ):
                forced_inactive = True
                break
        if forced_inactive:
            active[tid] = False
            continue

        deps = _effective_deps(task, graph)
        if not deps:
            active[tid] = True
            continue
        dep_active = [active.get(d, True) for d in deps]
        active[tid] = all(dep_active) if task.join == "all" else any(dep_active)
    return active


def _check_any_join_satisfiability(
    workflow: WorkflowSpec,
    graph: Graph,
    cones: dict[str, dict[str, set[str]]],
    membership: dict[str, set[tuple[str, str]]],
    join_any_tasks: list[TaskSpec],
) -> None:
    """Rule 7: for each `join="any"` task, brute-force every possible route
    selection (one route per router) and check whether the task is ever active.

    Raises SpecValidationError if a task is dead (never active) under EVERY
    possible verdict combination — i.e. no selection can ever activate it.
    """
    tasks_by_id = {t.id: t for t in workflow.tasks}
    order = graph.topological_order()
    router_ids = [r.id for r in workflow.branches]
    route_options = [list(r.routes.keys()) for r in workflow.branches]

    total_combinations = 1
    for opts in route_options:
        total_combinations *= max(len(opts), 1)
    if total_combinations > _MAX_ANY_JOIN_COMBINATIONS:
        # Pathological number of routers/routes — skip exhaustive enforcement
        # rather than hang `ao validate` (safe-by-default: a missed check here
        # is far less harmful than a stuck CLI).
        return

    ever_active = dict.fromkeys(t.id for t in join_any_tasks)
    for combo in product(*route_options):
        selection = dict(zip(router_ids, combo, strict=True))
        active = _simulate_route_selection(tasks_by_id, graph, cones, membership, order, selection)
        for t in join_any_tasks:
            if active.get(t.id):
                ever_active[t.id] = True
        if all(ever_active.values()):
            break  # every join="any" task already proven satisfiable

    for t in join_any_tasks:
        if not ever_active[t.id]:
            deps = sorted(_effective_deps(t, graph))
            raise SpecValidationError(
                f"Task {t.id!r}: join='any' is unsatisfiable — every effective "
                f"dependency {deps} sits in a route-cone that can never "
                "co-activate with any cone reachable by this task under any "
                "verdict selection",
                path=f"tasks.{t.id}.join",
            )


def validate_run_control(workflow: WorkflowSpec, graph: Graph) -> list[str]:
    """Static routing + circuit-breaker validation (LLD §4.4, rules 1-11).

    MUST run after `build_dag` (and ideally after `graph.topological_order()`
    has confirmed the graph is acyclic) — cone/reachability computation needs
    the full runtime graph (declared + inferred edges).

    Raises `SpecValidationError` (naming the offending ids/path) for rules
    1-5 and 7-11. Rule 6 is non-fatal: it is returned as a list of warning
    strings for the caller to print (see `cli._load_all`).
    """
    warnings: list[str] = []
    task_ids = {t.id for t in workflow.tasks}
    tasks_by_id = {t.id: t for t in workflow.tasks}
    adj = graph.adjacency()

    # Rule 11 — checked first so a malformed id fails fast, before any
    # reachability computation that might otherwise behave oddly on garbage ids.
    for router in workflow.branches:
        _check_reserved_id(router.id, f"branches.{router.id}.id")
        for route_id in router.routes:
            _check_reserved_id(route_id, f"branches.{router.id}.routes.{route_id}")
    for breaker in workflow.circuit_breakers:
        _check_reserved_id(breaker.id, f"circuit_breakers.{breaker.id}.id")

    # Rules 1-4: per-router reference/reachability/disjointness checks.
    for router in workflow.branches:
        # Rule 1: unknown router task.
        if router.router_task_id not in task_ids:
            raise SpecValidationError(
                f"Router {router.id!r}: unknown router_task_id {router.router_task_id!r}",
                path=f"branches.{router.id}.router_task_id",
            )

        router_reach = forward_closure(adj, [router.router_task_id])
        reach_by_route: dict[str, set[str]] = {}
        for route_id, route in router.routes.items():
            for entry in route.entry:
                # Rule 2: unknown entry.
                if entry not in task_ids:
                    raise SpecValidationError(
                        f"Router {router.id!r} route {route_id!r}: unknown entry {entry!r}",
                        path=f"branches.{router.id}.routes.{route_id}.entry",
                    )
                # Rule 3: entry must be reachable from router_task_id, else it
                # can never be deactivated by this router.
                if entry != router.router_task_id and entry not in router_reach:
                    raise SpecValidationError(
                        f"Router {router.id!r}: entry {entry!r} of route {route_id!r} is "
                        f"not reachable from router_task_id {router.router_task_id!r}; it "
                        "could never be deactivated by this router",
                        path=f"branches.{router.id}.routes.{route_id}.entry",
                    )
            reach_by_route[route_id] = forward_closure(adj, route.entry)

        # Rule 4: route-entry disjointness — no entry of route A may be
        # reachable from route B's entries (selecting B would force A active).
        for route_id_a, route_a in router.routes.items():
            for route_id_b, reach_b in reach_by_route.items():
                if route_id_a == route_id_b:
                    continue
                for entry in route_a.entry:
                    if entry in reach_b:
                        raise SpecValidationError(
                            f"Router {router.id!r}: route {route_id_a!r} entry {entry!r} "
                            f"is reachable from route {route_id_b!r} (entries "
                            f"{router.routes[route_id_b].entry}) — routes are not "
                            "disjoint; selecting one route would force the other "
                            "active too",
                            path=f"branches.{router.id}.routes.{route_id_a}.entry",
                        )

    # Cones/membership over the full runtime graph (T-c4w6p1) — feeds rules 5-9.
    cones, membership = compute_cones(workflow, graph)

    # Rule 9: nested router rejection (MVP boundary) — a router_task_id must
    # not lie inside another router's exclusive cone.
    for router in workflow.branches:
        for other in workflow.branches:
            if other.id == router.id:
                continue
            for route_id, cone in cones.get(other.id, {}).items():
                if router.router_task_id in cone:
                    raise SpecValidationError(
                        f"Router {router.id!r}: router_task_id "
                        f"{router.router_task_id!r} lies inside router {other.id!r}'s "
                        f"exclusive cone (route {route_id!r}); nested routers are not "
                        "supported in this release",
                        path=f"branches.{router.id}.router_task_id",
                    )

    # Rules 5-6: convergence-task checks, grouped per-router (a task reachable
    # from >=2 routes of the SAME router is a convergence point).
    per_task_router_routes: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for tid, pairs in membership.items():
        for router_id, route_id in pairs:
            per_task_router_routes[tid][router_id].add(route_id)

    for tid, by_router in per_task_router_routes.items():
        converging_router_ids = sorted(rid for rid, routes in by_router.items() if len(routes) >= 2)
        if not converging_router_ids:
            continue
        task = tasks_by_id[tid]

        # Rule 5 (R2, the important one): ANY inferred incoming edge into a
        # multi-route convergence task is rejected outright, regardless of
        # `join` — legitimate convergence must be declared (depends_on + join).
        for inp in task.inputs:
            producer = graph.producer_of(inp)
            if producer is not None and producer not in task.depends_on:
                raise SpecValidationError(
                    f"Task {tid!r} is reachable from >=2 routes of router(s) "
                    f"{converging_router_ids} via an inferred edge from "
                    f"{producer!r} (shared path {inp!r}); give distinct output "
                    "paths or declare this dependency explicitly "
                    "(depends_on + join)",
                    path=f"tasks.{tid}.inputs",
                )

        # Rule 6 (WARN only): declared multi-route convergence left at the
        # default join="all". Pydantic can't distinguish "author explicitly
        # wrote all" from "left at default" (both are just `join == 'all'`), so
        # this always warns on a declared-edge multi-route convergence with
        # join=='all' — simplest interpretation that still surfaces the risk
        # without over-engineering an explicit-vs-default tracking mechanism.
        if task.join == "all":
            warnings.append(
                f"Task {tid!r} converges >=2 routes of router(s) "
                f"{converging_router_ids} via declared depends_on but join is "
                "'all' (default); set join explicitly (or add join=\"any\" if "
                "only one branch need succeed) if this convergence is intentional"
            )

    # Rule 7: any-join satisfiability — full route-selection simulation.
    join_any_tasks = [t for t in workflow.tasks if t.join == "any"]
    if join_any_tasks and workflow.branches:
        _check_any_join_satisfiability(workflow, graph, cones, membership, join_any_tasks)

    # Rule 8: unreachable endpoint — every route must contain >=1 sink (a task
    # with no successors) within its exclusive cone.
    for router in workflow.branches:
        for route_id, cone in cones.get(router.id, {}).items():
            if not any(not adj.get(tid) for tid in cone):
                raise SpecValidationError(
                    f"Router {router.id!r} route {route_id!r}: no sink (task with no "
                    "successors) in its exclusive cone; this route can never reach a "
                    "terminal endpoint",
                    path=f"branches.{router.id}.routes.{route_id}",
                )

    # Rule 10: breaker refs — unknown verdict.task_id, duplicate ids, and
    # not-yet-implemented conditions.
    seen_breaker_ids: set[str] = set()
    for breaker in workflow.circuit_breakers:
        if breaker.id in seen_breaker_ids:
            raise SpecValidationError(
                f"Duplicate circuit breaker id: {breaker.id!r}",
                path=f"circuit_breakers.{breaker.id}.id",
            )
        seen_breaker_ids.add(breaker.id)

        if breaker.condition not in _MVP_BREAKER_CONDITIONS:
            raise SpecValidationError(
                f"Circuit breaker {breaker.id!r}: condition {breaker.condition!r} not "
                "implemented in this release",
                path=f"circuit_breakers.{breaker.id}.condition",
            )
        if breaker.condition == "verdict" and breaker.task_id not in task_ids:
            raise SpecValidationError(
                f"Circuit breaker {breaker.id!r}: unknown verdict task_id {breaker.task_id!r}",
                path=f"circuit_breakers.{breaker.id}.task_id",
            )

    return warnings
