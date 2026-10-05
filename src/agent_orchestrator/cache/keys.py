"""`build_cache_key`: turn "what this agent would be asked to do" into a sha256 (HLD 8.2.6, 8.2.7).

The key document is canonical JSON of: the keyed `AgentSpec` fields, the exact argv (so any change
in argv construction changes keys), the executor fingerprint, the rendered prompt, the content
digests of every input / instruction / general instruction, the prior digest of every declared
output and the repo HEADs. Run id, task id, timeout, retries and the absolute workspace path are
never part of it. Any input that cannot be hashed safely raises `UncacheableError`.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from hashlib import sha256
from typing import Any

from pydantic import ValidationError

from agent_orchestrator.cache import safeio
from agent_orchestrator.cache.constants import (
    AGENT_KEY_FIELDS,
    CACHE_DIR_PARTS,
    COMPONENT_DIGEST_CHARS,
    KEY_PLACEHOLDER_ID,
    KEY_PLACEHOLDER_TIMEOUT,
    KEY_SCHEMA_VERSION,
    PRIOR_ABSENT,
    REASON_CONTROL_OUTPUT,
    REASON_DUPLICATE_OUTPUT,
    REASON_INPUT_MISSING,
    REASON_KEY_ENCODING,
    REASON_OUTPUT_NOT_REGULAR,
    REASON_PROMPT_RENDER_ERROR,
    REASON_SENSITIVE_OUTPUT,
)
from agent_orchestrator.cache.fingerprint import (
    claude_cli_fingerprint,
    guarded_abs,
    guarded_resolve,
)
from agent_orchestrator.cache.hashing import HashBudget, digest_path, hash_regular_file
from agent_orchestrator.cache.types import (
    CacheKey,
    Digest,
    KeyDeps,
    KeyRequest,
    KeySummary,
    UncacheableError,
    canonical_json,
)
from agent_orchestrator.executors.claude_cli import build_claude_argv
from agent_orchestrator.executors.prompt import build_prompt
from agent_orchestrator.models import TaskContext

# The one canonical serializer lives in `types` (neither `types` nor `hashing` may import this
# module); re-exported so key code and its tests have a single obvious import site.
__all__ = ["build_cache_key", "canonical_json", "summary_from_doc"]

_EXECUTOR_CLAUDE_CLI = "claude_cli"


def _component_digest(value: object) -> str:
    return sha256(canonical_json(value).encode("ascii")).hexdigest()[:COMPONENT_DIGEST_CHARS]


def summary_from_doc(doc: dict[str, Any], components: dict[str, str]) -> KeySummary:
    """The non-sensitive summary stored in the entry (D21): paths, model / effort / max_turns,
    display digests, repo heads and component digests. NEVER argv, prompt, `prompt_template` or
    `extra_args`.
    """
    agent = doc["agent"]
    files = [
        doc["instruction"],
        *doc["general_instructions"],
        *doc["inputs"],
        *doc["dynamic_inputs"],
    ]
    return KeySummary(
        key_schema=doc["key_schema"],
        executor=agent["executor"],
        model=agent["model"],
        effort=agent["effort"],
        max_turns=agent["max_turns"],
        instruction=doc["instruction"]["path"],
        inputs=[e["path"] for e in doc["inputs"]],
        dynamic_inputs=[e["path"] for e in doc["dynamic_inputs"]],
        general_instructions=[e["path"] for e in doc["general_instructions"]],
        outputs=[o["path"] for o in doc["outputs"]],
        digests={e["path"]: e["sha256"] for e in files if "sha256" in e},
        repo_heads=doc["repo_heads"],
        components=dict(components),
    )


def build_cache_key(
    req: KeyRequest, deps: KeyDeps, *, preseed: Mapping[str, Digest | None] | None = None
) -> CacheKey:
    """The deterministic cache key for *req* (HLD 8.2.6). *preseed* maps an absolute OUTPUT path to
    the digest it had at lookup time (None = absent), so a settle-time recompute, after the agent
    has overwritten an output that is also an input, reproduces the lookup-time key (N-5).
    """
    ws = req.workspace_root

    def rel(path: str) -> str:
        return safeio.posix_rel(path, ws)

    def guard(raw: str) -> str:
        return guarded_resolve(req, raw)

    def guard_abs(path: str) -> str:
        return guarded_abs(req, path)

    instr = guard(req.task.instruction)
    gis = [guard_abs(p) for p in req.general_instruction_paths]
    ins = [guard(p) for p in req.task.inputs]
    dyn = [guard(p) for p in req.dynamic_input_paths]
    outs = [guard(p) for p in req.task.outputs]
    if len(set(outs)) != len(outs):
        raise UncacheableError(REASON_DUPLICATE_OUTPUT)
    for o in outs:
        if safeio.is_sensitive_rel_path(rel(o)):  # D29 (also an output symlinked into .git/hooks)
            raise UncacheableError(REASON_SENSITIVE_OUTPUT, rel(o))
        if o in req.control_paths_abs:
            raise UncacheableError(REASON_CONTROL_OUTPUT, rel(o))
    repos = {rid: rel(guard_abs(p)) for rid, p in sorted(req.repo_paths.items())}

    budget = HashBudget(req.max_input_bytes, req.max_input_files)
    memo: dict[str, Digest | None] = dict(preseed or {})
    skip_abs = frozenset({os.path.join(ws, CACHE_DIR_PARTS[0])})
    exclude_abs = frozenset(outs)
    for o in outs:  # priors (D6)
        if o in memo:
            continue
        if not os.path.lexists(o):
            memo[o] = None
        else:
            if not stat.S_ISREG(os.lstat(o).st_mode):
                raise UncacheableError(REASON_OUTPUT_NOT_REGULAR, rel(o))
            memo[o] = hash_regular_file(o, budget)

    def digest_of(path: str) -> Digest:  # inputs; outputs reuse the memo (N-5)
        if path in memo:
            known = memo[path]
            if known is None:
                raise UncacheableError(REASON_INPUT_MISSING, rel(path))
            return known
        fresh = digest_path(path, budget, exclude_abs=exclude_abs, skip_abs=skip_abs)
        memo[path] = fresh
        return fresh

    def entry(path: str) -> dict[str, Any]:
        dg = digest_of(path)
        return {"path": rel(path), "kind": dg.kind, "sha256": dg.sha256, "size": dg.size}

    # The prompt is rendered from a NORMALIZED context: workspace-relative paths and placeholder
    # ids, so it is identical across runs, tasks and workspace locations (U-K7).
    normalized = TaskContext(
        run_id=KEY_PLACEHOLDER_ID,
        task_id=KEY_PLACEHOLDER_ID,
        agent=req.agent,
        instruction_path=rel(instr),
        general_instruction_paths=[rel(p) for p in gis],
        input_paths=[rel(p) for p in ins],
        output_paths=[rel(p) for p in outs],
        output_manifest_path=None,
        dynamic_input_paths=[rel(p) for p in dyn],
        repo_paths=repos,
        timeout_seconds=KEY_PLACEHOLDER_TIMEOUT,
    )
    try:
        prompt = build_prompt(normalized)
    except Exception as e:  # noqa: BLE001 -- any template failure (KeyError, IndexError, ...)
        raise UncacheableError(REASON_PROMPT_RENDER_ERROR, type(e).__name__) from e

    is_claude = req.agent.executor == _EXECUTOR_CLAUDE_CLI
    argv = build_claude_argv(req.agent, prompt) if is_claude else None
    fingerprint = claude_cli_fingerprint(req, deps, budget) if is_claude else None
    doc: dict[str, Any] = {
        "key_schema": KEY_SCHEMA_VERSION,
        "agent": {f: getattr(req.agent, f) for f in sorted(AGENT_KEY_FIELDS)},
        "argv": argv,
        "executor_fingerprint": fingerprint,
        "prompt": prompt,
        "instruction": entry(instr),
        "general_instructions": [entry(p) for p in gis],
        "inputs": [entry(p) for p in ins],
        "dynamic_inputs": [entry(p) for p in dyn],
        "outputs": sorted(
            (
                {
                    "path": rel(o),
                    "prior": PRIOR_ABSENT if (prior := memo[o]) is None else prior.sha256,
                }
                for o in outs
            ),
            key=lambda x: x["path"],
        ),
        "repo_heads": dict(req.repo_heads) if req.include_repo_heads else None,
    }
    try:
        text = canonical_json(doc)
        components = {name: _component_digest(value) for name, value in doc.items()}
    except (TypeError, ValueError) as e:
        raise UncacheableError(REASON_KEY_ENCODING, type(e).__name__) from e
    try:
        summary = summary_from_doc(doc, components)
    except ValidationError as e:  # a path / text over the entry-schema bounds cannot be stored
        raise UncacheableError(REASON_KEY_ENCODING, "summary_out_of_bounds") from e
    return CacheKey(
        key=sha256(text.encode("ascii")).hexdigest(),
        summary=summary,
        output_paths=tuple(sorted(rel(o) for o in outs)),
        output_abs={rel(o): o for o in outs},
        preseed={o: memo[o] for o in outs},
        components=components,
        cli_version=fingerprint["cli_version"] if fingerprint else None,
    )
