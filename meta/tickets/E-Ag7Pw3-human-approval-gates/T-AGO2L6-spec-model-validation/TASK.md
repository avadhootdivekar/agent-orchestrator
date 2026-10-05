# TASK: T-AGO2L6-spec-model-validation

## Metadata
- Task ID: `T-AGO2L6-spec-model-validation`
- Epic ID: `E-Ag7Pw3-human-approval-gates`
- Owner: developer
- Created: 2026-10-04
- Last Updated: 2026-10-05 (rev 3)
- Status: Draft
- Estimate: 3 days (rev 1: 2.5 d; Gate 1 added null parity, the wrap serializer + golden, AG-5, W-AG-9/10
  and the rev-2 models; rev 3 adds the marker model, keeps one W-AG-7 call site (W-AG-8 withdrawn) and narrows
  `conftest.py` to the autouse hermeticity fixtures). Deliver `approvals/models.py` + `approvals/errors.py` merged by the end of day 1 —
  they unblock `T-drPIif` and `T-1MgGb4`.

## Requirements Mapping
- Requirement IDs: FR-1, FR-7 (manifest containment), NFR-1, NFR-5, NFR-9
- Design: HLD §9 constants table, §9.1 (all, incl. AG-4/AG-5, W-AG-9/10, null parity, the wrap
  serializer and the string-sentinel rationale), §9.2.1–§9.2.3, §8.2–§8.3 (layering), §26 rows 1–6, 8, 9;
  tensions T-2, T-3, T-13; Gate 1 suggestions S-01, S-02, S-07, S-09, S-11

## Description
Make `approval` a first-class, validated `TaskSpec` field and add every persisted approval model.

1. New package skeleton: `src/agent_orchestrator/approvals/__init__.py` (docstring only, **no imports**),
   `approvals/models.py` (**all** constants of the HLD §9 table, incl. `CONFIG_ENV_DENYLIST`,
   `MAX_JSON_DEPTH`, `MAX_JSON_INT_DIGITS`, `MAX_REHASH_BYTES_PER_POLL`, `TASK_ID_PARAM_PATTERN`,
   `MAX_GATE_ID_CHARS`, `ITER_CLONE_PATTERN`, `APPROVALS_DIRNAME`, `MAX_POLICY_DETAILS`,
   `MAX_CLOSED_WAITS`, `GATED_MARKER_DIRNAME`, `GATED_MARKER_KIND`, `MAX_GATED_MARKER_BYTES` (rev 3:
   no `MAX_REHASH_BYTES_PER_POLL`, no `MAX_UNREVIEWED_ITEMS`); `ApprovalSpec`, `ReviewDigest`,
   `DecisionIdentity`, `ApprovalRequest`, `ApprovalStatus`, `PreviousRequest`, `WaitSpan`, `ApprovalState`
   (with `closed_waits`), `GatePolicy`, `ApprovalPolicyRecord` (gate-scoped, with `updated_at` and the
   signed `previous_sha256`, rev 3), `GatedMarker` (rev 3, HLD §9.3.4),
   `DecisionRecord`, `RefusalReason` and `RefusalDetail` (closed `StrEnum`s of HLD §9.5.3) and the
   `REFUSAL_HTTP_STATUS` / `REFUSAL_EXIT_CODE` tables of HLD §9.14.3), `approvals/errors.py`
   (`ApprovalError(OrchestratorError)` and every subclass named in HLD §9.3, §9.10, §9.12: incl.
   `ApprovalPolicyError`, `ApprovalStateError`, `ApprovalSignerError` + `ApprovalRefused` /
   `ApprovalIntegrityError` / `ApprovalNotFound`, `RecordMalformed`, `ApprovalKeyMissing` /
   `ApprovalKeyInsecure` / `ApprovalKeyUnavailable`, `ApprovalTamperError`, `GatedMarkerInvalid`,
   `GatedMarkerUnwritable`). Later tasks only import from
   these two files.
2. `models.py`: import from `approvals.models` (and `model_validator`, `model_serializer`,
   `SerializerFunctionWrapHandler` from pydantic); `TaskSpec.agent: str = ""` and
   `TaskSpec.instruction: str = ""` (string sentinels — types unchanged), `TaskSpec.approval`;
   `_check_approval_gate_shape` + `_approval_gate_shape_problems` exactly per HLD §9.1.2; the
   `_omit_absent_approval` wrap serializer (§9.1.2, Gate 1 suggestion S-02); `TaskStatus +=
   "awaiting_approval"`; `RunState.approvals`, `RunState.approval_policy`; `_is_structural_task` returns
   True for gates; `resolve_task_isolation` skips its "structural task asked for worktree" warning for
   gates (HLD §9.1.8); docstrings mention approval gates.
3. `specs/workflow.schema.json`: HLD §9.1.3 (task `required: ["id"]`, `approval` property
   `anyOf [null, $ref]`, `allOf` `if` (`approval` present **and an object**) / `then` / `else`, with
   `agent`/`instruction` `maxLength: 0` for gates, `$defs.approval`).
4. `approvals/spec_rules.py::validate_approval_gates(workflow) -> list[str]`: AG-1..AG-5 raise
   `SpecValidationError` with the documented message/path; W-AG-1..W-AG-6, W-AG-9 and W-AG-10 returned,
   using `dag.iter_dependency_edges` + `dag.forward_closure` (never `build_dag`, which logs). AG-4 uses an
   explicit-`depends_on`-only adjacency restricted to the loop body. Leave one clearly marked call site for
   W-AG-7, which `T-Mdk27e` wires in during stage E (rev 3); W-AG-8 is withdrawn and not implemented.
5. `spec.py::cross_validate`: gate-aware agent check + call `validate_approval_gates` and merge its
   warnings before the isolation warnings (HLD §9.1.4); `spec.validate_isolation` V4 skips gates.
6. `artifacts.py::read_task_manifest`: reject any task dict containing the key `approval` (HLD §9.1.6).
7. `tests/approvals/test_import_layering.py` — **sole owner** (Gate 1 parallelisation map): every rule of
   HLD §8.3 (1–5) written now, parametrized over the planned module list; a case whose module does not
   exist yet calls `pytest.skip` with the owning task id in the reason (`T-pdLR96` asserts none is
   skipped at the end).
8. `tests/approvals/conftest.py` with the **autouse hermeticity fixtures only** (rev 3, Gate 2 R-09): key
   dir from `tmp_path_factory.mktemp("approval-keys") / "keys"`, `AO_IN_AGENT` and
   `AO_APPROVAL_POLL_SECONDS` removed; plus `tests/approvals/test_hermeticity.py::test_fixture_hermeticity`
   (HLD §18.2). The shared `fake_clock` / `make_gated_workflow` / `human_decides` fixtures are added later
   by `T-pfJiXw`; the engine helpers live in `T-vwIpSw`'s `engine_helpers.py`.

Files — new: `src/agent_orchestrator/approvals/{__init__,models,errors,spec_rules}.py`,
`tests/approvals/{__init__,conftest}.py` (autouse fixtures only), `tests/approvals/test_spec_validation.py`,
`tests/approvals/test_import_layering.py`, `tests/approvals/test_hermeticity.py`. Shared (HLD §26): `models.py` (#1–#5), `spec.py` (#6),
`specs/workflow.schema.json` (#8), `artifacts.py` (#9). No `engine.py` edit.
**Exclusive files during stage A** (HLD §22.3): all of the above; `T-1B8hu4` must not start before this
task has merged (`spec.py`).

## Acceptance Criteria
1. `import agent_orchestrator.approvals.models` in a fresh interpreter imports no other
   `agent_orchestrator` module; importing `agent_orchestrator.models` imports only `approvals.models` from
   the new package (`test_import_layering.py`; the other rules exist and skip until their modules land).
2. Table-driven test: every row of HLD §9.1.2's gate table is rejected by pydantic with its message
   fragment **and** by the JSON Schema (`jsonschema.validate` against `specs/workflow.schema.json`) —
   `TestSchemaPydanticParity` asserts identical verdicts for the whole table, including the
   `"approval": null` rows (null + agent + instruction accepted; null without agent or instruction
   rejected; `"approval": "yes"` rejected) (Gate 1 suggestion S-01).
3. A gate with `approval` and no `agent`/`instruction` (or with `""` for either) loads; a gate with a
   non-empty `agent` or `instruction` fails; a non-gate without the `agent` key fails with
   `'agent' is required`; a non-gate without the `instruction` key fails with `'instruction' is required`;
   a non-gate with `"instruction": ""` still loads. `mypy` on `src/` and the pre-existing bench tests shows
   no new error (types unchanged).
4. `review` omitted → equals `inputs` after load; explicit `review` kept; `model_copy(deep=True,
   update={"inputs": []})` keeps `review`; `TaskSpec.model_validate(t.model_dump()) == t` for a gate and a
   non-gate; the round-trip invariant test covers every task of `specs/examples/workflow*.json`.
5. `test_gate_free_spec_dump_and_sha_unchanged` (Gate 1 suggestion S-02): for every
   `specs/examples/workflow*.json`, `canonical_spec_json(load_workflow(p))` and its sha256 equal goldens
   **recorded from the unmodified models at the start of this task**; a gate's dump contains `approval`;
   `"approval": null` is omitted on dump.
6. `cross_validate`: AG-1..AG-5 raise `SpecValidationError` with the exact `path` of HLD §9.1.4 (AG-4 as in
   rev 1; `test_ag5_gate_id_pattern_and_length`: a 201-character gate id and one with a `/` are rejected);
   W-AG-1..W-AG-6, W-AG-9 (`test_w_ag_9_dashboard_only_gate`, with and without the "waits forever" suffix)
   and W-AG-10 (`test_w_ag_10_never_synced_checkout`, for `integration.sync_checkout: never` and
   `integration.workspace_lock: skip_sync` — the `IntegrationSpec` fields, Gate 2 R-12) each returned for a fixture that triggers it and absent otherwise; an
   unknown agent on a non-gate still raises the pre-existing error; `ao validate` (CliRunner) prints the
   warnings as `WARNING: …` lines and **no** V4/runtime isolation warning for a gate under
   `defaults.isolation: worktree`.
7. `read_task_manifest` raises `ValueError` naming the task index for a task containing `approval` (also
   for `"approval": null`); every pre-existing manifest test passes unedited.
8. `resolve_task_isolation(gate, workflow_with_defaults_worktree) == "none"`.
9. The pre-epic state fixture `tests/fixtures/state_pre_routing_breakers.json` (and a dict dumped from a
   pre-epic-shaped `RunState` with injected tasks) loads with `approvals == {}` and
   `approval_policy is None`.
10. Full suite (stage-A checkpoint, run by the manager): no new failures vs the brief baseline; no
    pre-existing test file edited (`tests/test_nfr2_regression_gate.py` green); `ruff check`,
    `ruff format --check` and `mypy` clean on every changed module.
11. A `reviewer`-agent review is recorded in STATUS.md (no open BLOCKER/MAJOR).

## Risks
- `model_fields_set` rule for `instruction` depends on `TaskSpec` never being dumped with
  `exclude_unset/exclude_defaults` (AC4 pins it).
- pydantic `model_validator(mode="after")` assigning `self.approval` — verify no recursion/validation
  loop (assignment validation is off by default).
- The wrap serializer must not change any gate-free dump (AC5 is the guard; verified at design time on all
  10 example specs).

## Dependencies
- Design re-gate passed (T-FjxjlV rev 2) and the parent session's decision points confirmed.

## Pseudocode / Algorithm
```text
See HLD §9.1.2 (validator + serializer), §9.1.4 (validate_approval_gates table), §9.1.6 (manifest check).
W-AG-3: adj = {} ; for e in iter_dependency_edges(wf): adj.setdefault(e.source, []).append(e.target)
        for emitter in tasks with emit_tasks: for gate in forward_closure(adj, [emitter]) & gate_ids: warn
AG-5:   for gate: if len(id) > MAX_GATE_ID_CHARS or not TASK_ID_PARAM_PATTERN.fullmatch(id): raise
```

## Schemas / Interface Notes
- Interface / API: `ApprovalSpec`, `TaskSpec` changes, `validate_approval_gates(workflow) -> list[str]`,
  the refusal enums and mapping tables.
- Spec / data schema: HLD §9.1.1–§9.1.3, §9.2.1.
- Triggers / events: N/A.
- Artifacts: none.

## Verification
```
WT=/usr/avadhoot/mounted/agent-orchestrator/.claude/worktrees/agent-a513b4d78ac3b20bb
PY=/usr/avadhoot/mounted/agent-orchestrator/.venv/bin/python
cd $WT && $PY -m pytest -o pythonpath=src -p no:cacheprovider -q tests/approvals/test_spec_validation.py tests/approvals/test_import_layering.py
cd $WT && $PY -m pytest -o pythonpath=src -p no:cacheprovider -q
cd $WT && $PY -m ruff check src/agent_orchestrator tests/approvals && $PY -m ruff format --check src/agent_orchestrator/approvals src/agent_orchestrator/models.py src/agent_orchestrator/spec.py src/agent_orchestrator/artifacts.py tests/approvals
cd $WT && $PY -m mypy src/agent_orchestrator/approvals src/agent_orchestrator/models.py src/agent_orchestrator/spec.py src/agent_orchestrator/artifacts.py src/agent_orchestrator/engine.py tests/bench/test_ao_epic_plus_subject.py tests/bench/test_dev_core_suite.py
```

## Handoff Boundary
- Upstream: HLD §9.1/§9.2 (frozen at task start).
- Downstream: `T-drPIif` and `T-1MgGb4` (use `approvals/models.py`/`errors.py` from day 2), `T-1B8hu4`
  (after merge), `T-pfJiXw` (appends shared fixtures to `conftest.py`), `T-Mdk27e` (W-AG-7 wiring), every
  later task.

## Artifacts
- Code as listed; evidence in STATUS.md.
