"""Workflow templates — manifest parsing, discovery, rendering, instantiation (E-Tpl3x9).

A *template* is a directory containing a ``template.yaml`` manifest (HLD §2.2) plus file
templates. Templates are defined at config/install time (never authored via the UI) and
*instantiated* many times via :func:`instantiate`, each call producing a self-contained run
instance (a directory with a rendered ``workflow.json``/``prompt.md``/aux files) that the
existing run/resume/status machinery operates on unchanged.

Public API (HLD §2.4) — :class:`TemplateError`, :class:`TemplateParam`, :class:`TemplateInfo`,
:class:`InstantiateResult`, :func:`discover_templates`, :func:`load_template`,
:func:`instantiate`.

Design notes
------------
* **No new runtime dependency for rendering** (NFR-1) — a small regex-based ``{{ var }}``
  substitution replaces the (evaluated) need for Jinja2. There are no conditionals/loops;
  branching lives in the manifest's ``when:`` gate, keeping templates declarative.
* **Two, deliberately distinct, templating syntaxes**: ``{{ var }}`` (double brace) inside
  *file contents* is resolved by :func:`_render` against a small fixed variable set (``id``,
  ``slug``, ``instance_dir``, ``workspace_root``, ``params.<name>``). ``{token}`` (single
  brace) inside the manifest's own ``id_pattern``/``instance_dir`` *path strings* is resolved
  by simple ``str.replace`` before any instance exists yet (there is no ``id``/``instance_dir``
  variable to substitute into `` instance_dir`` -- it IS ``instance_dir``). Conflating the two
  would make ``instance_dir: "...{id}"`` ambiguous with content rendering; keeping them
  separate keeps both simple.
* ``TemplateInfo`` carries only what HLD §2.4 documents (list-friendly, JSON/dataclass-
  serializable for the future dashboard API, HLD §2.6) -- it does NOT cache the full parsed
  manifest (dirs/files/assets/id_pattern). :func:`instantiate` re-parses ``template.path`` via
  the same private loader :func:`load_template` uses. This is a deliberate, small, local
  double-parse (a few KB of YAML) traded for a public dataclass that stays exactly what the
  contract says and safely `dataclasses.asdict`-serializable -- the same trade-off
  ``cli._load_project_config_or_none`` documents for project config.
"""

from __future__ import annotations

import json
import posixpath
import random
import re
import string
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from pydantic import ValidationError as PydanticValidationError

from ..artifacts import LocalFsArtifactStore
from ..errors import ArtifactPathError, OrchestratorError
from ..project_config import ProjectConfig
from ..spec import load_workflow

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class TemplateError(Exception):
    """Raised for template manifest / discovery / rendering / instantiate failures.

    Deliberately a plain ``Exception`` (HLD §2.4), not ``OrchestratorError``: templates are
    a CLI/dashboard *scaffolding* concern that produces ordinary workflow/reposet/agent spec
    files on disk. Once an instance is scaffolded it is validated and run through the
    existing ``OrchestratorError``-based paths (``ao validate`` / ``ao run``) unchanged.
    """


@dataclass(frozen=True)
class TemplateParam:
    name: str
    description: str = ""
    required: bool = False
    enum: list[str] | None = None
    default: str | None = None


@dataclass(frozen=True)
class TemplateInfo:
    name: str
    description: str
    path: str  # absolute path to the template directory (contains template.yaml)
    source: Literal["builtin", "workspace", "path"]
    params: list[TemplateParam]
    required_agents: list[str]
    prompt_skeleton: str | None  # rendered-with-placeholders preview of prompt.md, if any


@dataclass(frozen=True)
class InstantiateResult:
    instance_dir: str  # absolute
    workflow_path: str  # absolute
    prompt_path: str | None  # absolute
    created: list[str]  # workspace-relative, for reporting
    skipped: list[str]  # workspace-relative, for reporting


# ---------------------------------------------------------------------------
# Constants (named, not magic literals — CLAUDE.md)
# ---------------------------------------------------------------------------

# template.yaml's default id_pattern when a manifest doesn't set one (HLD §2.2).
_DEFAULT_ID_PATTERN = "e-{rand6}-{slug}"

# Single-brace path-template tokens (id_pattern / instance_dir) -- distinct from the
# double-brace {{ var }} content renderer, see module docstring.
_ID_PATTERN_TOKEN_RE = re.compile(r"\{(rand6|slug)\}")
_INSTANCE_DIR_TOKEN_RE = re.compile(r"\{(id|slug)\}")

# A bare slug: lowercase alnum, first char alnum, hyphens allowed after.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
# Template manifest `name:` -- kept simple since it doubles as the `ao new <name>` token.
_TEMPLATE_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")
# A declared param name -- a plain identifier so "params.<name>" composes unambiguously.
_PARAM_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Double-brace content-render variable reference, e.g. "{{ params.type }}".
_VAR_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\}\}")

# Conventional basenames the rendered workflow spec must appear under (HLD §2.3).
_WORKFLOW_BASENAMES = ("workflow.json", "workflow.yaml", "workflow.yml")

# `when:` prefix accepted per HLD §2.2's documented form ("when: params.type"). The bare
# form ("when: type", no prefix) is ALSO accepted for robustness -- both name the same
# param unambiguously and the bare form is what the shipped built-in `routed-runner`
# template (T-Tb3rtr, developed concurrently against this same HLD) actually uses.
_WHEN_PARAM_PREFIX = "params."

# Built-in template discovery root (HLD §2.1 point 2) -- package data shipped in the wheel.
# A module-level constant (rather than computed inline in `discover_templates`) so tests can
# `monkeypatch.setattr(templates, "_BUILTIN_ROOT", tmp_path)` to exercise discovery/shadowing
# without depending on (or mutating) the real built-in templates.
_BUILTIN_ROOT = Path(__file__).parent / "builtin"


# ---------------------------------------------------------------------------
# Manifest schema (pydantic, matching project_config.py's style)
# ---------------------------------------------------------------------------


class _ParamSpec(BaseModel):
    """One entry of template.yaml's `params:` mapping (name -> spec)."""

    # Manifest authoring is config-time and static; `extra="forbid"` catches typos
    # (e.g. "requried") loudly instead of silently ignoring them -- a stricter posture
    # than ProjectConfig's tolerant default, which exists for a different reason
    # (cross-version CLI backward compatibility of a *runtime* file, HLD §2.1).
    model_config = ConfigDict(extra="forbid")

    description: str = ""
    required: bool = False
    enum: list[str] | None = None
    default: str | None = None

    @model_validator(mode="after")
    def _default_in_enum(self) -> _ParamSpec:
        if self.enum is not None and self.default is not None and self.default not in self.enum:
            raise ValueError(f"default {self.default!r} is not one of enum {self.enum}")
        return self


class _FileEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str | None = None  # template-dir-relative
    content: str | None = None  # inline alternative to source
    target: str  # instance_dir-relative
    keep_existing: bool = False
    when: str | None = None  # "params.<name>" or bare "<name>"

    @field_validator("target", "source")
    @classmethod
    def _no_traversal(cls, v: str | None) -> str | None:
        if v is not None:
            _reject_traversal(v)
        return v

    @model_validator(mode="after")
    def _source_xor_content(self) -> _FileEntry:
        if (self.source is None) == (self.content is None):
            raise ValueError(
                f"files entry {self.target!r}: exactly one of 'source' or 'content' must be set"
            )
        return self


class _AssetEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str  # template-dir-relative (file or dir)
    target: str  # workspace-root-relative
    keep_existing: bool = False

    @field_validator("source", "target")
    @classmethod
    def _no_traversal(cls, v: str) -> str:
        _reject_traversal(v)
        return v


class _TemplateManifest(BaseModel):
    """The parsed, validated `template.yaml` (HLD §2.2)."""

    model_config = ConfigDict(extra="forbid")

    version: str
    name: str
    description: str = ""
    id_pattern: str = _DEFAULT_ID_PATTERN
    instance_dir: str
    params: dict[str, _ParamSpec] = {}
    dirs: list[str] = []
    files: list[_FileEntry] = []
    assets: list[_AssetEntry] = []
    required_agents: list[str] = []

    @field_validator("name")
    @classmethod
    def _name_pattern(cls, v: str) -> str:
        if not _TEMPLATE_NAME_RE.match(v):
            raise ValueError(
                f"name {v!r} must match pattern '{_TEMPLATE_NAME_RE.pattern}' "
                "(lowercase kebab-case, used as the `ao new <name>` token)"
            )
        return v

    @field_validator("instance_dir")
    @classmethod
    def _instance_dir_relative(cls, v: str) -> str:
        if not v or v.startswith("/"):
            raise ValueError(f"instance_dir must be a non-empty, workspace-relative path: {v!r}")
        _reject_traversal(v)
        return v

    @field_validator("dirs")
    @classmethod
    def _dirs_no_traversal(cls, v: list[str]) -> list[str]:
        for d in v:
            _reject_traversal(d)
        return v

    @field_validator("params")
    @classmethod
    def _param_names_valid(cls, v: dict[str, _ParamSpec]) -> dict[str, _ParamSpec]:
        for name in v:
            if not _PARAM_NAME_RE.match(name):
                raise ValueError(
                    f"param name {name!r} must match pattern '{_PARAM_NAME_RE.pattern}'"
                )
        return v

    @model_validator(mode="after")
    def _cross_validate(self) -> _TemplateManifest:
        if "{slug}" not in self.id_pattern:
            raise ValueError(f"id_pattern must contain '{{slug}}': {self.id_pattern!r}")
        for f in self.files:
            if f.when is not None:
                pname = _strip_when_prefix(f.when)
                if pname not in self.params:
                    raise ValueError(
                        f"files[{f.target!r}].when references undeclared param {pname!r}"
                    )
        return self


def _reject_traversal(value: str) -> None:
    if ".." in re.split(r"[\\/]", value):
        raise ValueError(f"path must not contain '..' segments: {value!r}")
    # B1 fix: reject absolute paths too. `Path(template_dir) / value` silently DISCARDS
    # template_dir when `value` is absolute (that's how `pathlib.Path.__truediv__` works),
    # so an absolute `source:`/`target:` would otherwise escape the template dir entirely
    # with no error -- this check applies to every field routed through `_reject_traversal`
    # (files[].source/target, assets[].source/target, dirs[]), closing the gap at
    # manifest-validation time, before any file IO happens.
    if Path(value).is_absolute():
        raise ValueError(f"path must be relative, not absolute: {value!r}")


def _strip_when_prefix(when: str) -> str:
    """Normalize a `when:` value to the bare param name.

    Accepts both the HLD §2.2-documented "params.<name>" form and the bare "<name>" form
    (see `_WHEN_PARAM_PREFIX` for why both are supported).
    """
    return when[len(_WHEN_PARAM_PREFIX) :] if when.startswith(_WHEN_PARAM_PREFIX) else when


def _flatten_pydantic_error(exc: PydanticValidationError) -> str:
    # Small, intentional duplicate of project_config.py's identical one-liner (kept local
    # rather than extracted cross-module for this narrowly-scoped task; see CLAUDE.md DRY
    # note -- three lines duplicated once does not justify a shared-helper module edit here).
    return "; ".join(f"{'.'.join(str(loc) for loc in e['loc'])}: {e['msg']}" for e in exc.errors())


# ---------------------------------------------------------------------------
# Manifest loading
# ---------------------------------------------------------------------------


def _load_manifest(template_dir: Path) -> _TemplateManifest:
    manifest_path = template_dir / "template.yaml"
    if not manifest_path.is_file():
        raise TemplateError(f"template manifest not found: {manifest_path}")
    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TemplateError(f"cannot read {manifest_path}: {exc}") from exc
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        raise TemplateError(f"invalid YAML in {manifest_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise TemplateError(f"{manifest_path}: must be a YAML mapping, got {type(data).__name__}")
    try:
        return _TemplateManifest.model_validate(data)
    except PydanticValidationError as exc:
        raise TemplateError(
            f"invalid template manifest {manifest_path}: {_flatten_pydantic_error(exc)}"
        ) from exc


def _safe_join(template_dir: Path, rel: str, desc: str) -> Path:
    """Resolve *rel* against *template_dir* and verify containment (B1 defense-in-depth).

    Mirrors `LocalFsArtifactStore.resolve()`'s write-side guard: normalize-then-check-
    containment via `.resolve()` + `.is_relative_to()`, rather than trusting the literal
    joined path. `_reject_traversal` already rejects absolute paths and literal `..`
    segments at manifest-validation time; this additionally catches relative-traversal-
    through-symlinks (a symlink inside the template dir pointing outside it) since
    `.resolve()` follows symlinks before the containment check runs.
    """
    template_dir_resolved = template_dir.resolve()
    candidate = (template_dir / rel).resolve()
    if not candidate.is_relative_to(template_dir_resolved):
        raise TemplateError(f"{desc}: source {rel!r} escapes the template directory")
    return candidate


def _read_template_file(template_dir: Path, rel: str, template_name: str) -> str:
    p = _safe_join(template_dir, rel, f"template {template_name!r}")
    if not p.is_file():
        raise TemplateError(f"template {template_name!r}: source file not found: {p}")
    try:
        return p.read_text(encoding="utf-8")
    except OSError as exc:
        raise TemplateError(f"template {template_name!r}: cannot read {p}: {exc}") from exc


def _find_prompt_entry(manifest: _TemplateManifest) -> _FileEntry | None:
    """The `files[]` entry that renders the instance's prompt.md, if any (by basename)."""
    for f in manifest.files:
        if Path(f.target).name == "prompt.md":
            return f
    return None


def _preview_variables(manifest: _TemplateManifest) -> dict[str, str]:
    """Placeholder variable map used ONLY to render `prompt_skeleton` at discovery time,
    before any instance/id/params exist (HLD: "rendered-with-placeholders preview")."""
    variables = {
        "id": "<id>",
        "slug": "<slug>",
        "instance_dir": "<instance_dir>",
        "workspace_root": "<workspace_root>",
    }
    for name, spec in manifest.params.items():
        variables[f"params.{name}"] = spec.default if spec.default is not None else f"<{name}>"
    return variables


def _load_template_info(
    template_dir: Path, source: Literal["builtin", "workspace", "path"]
) -> TemplateInfo:
    manifest = _load_manifest(template_dir)

    params = [
        TemplateParam(
            name=name,
            description=spec.description,
            required=spec.required,
            enum=spec.enum,
            default=spec.default,
        )
        for name, spec in manifest.params.items()
    ]

    prompt_entry = _find_prompt_entry(manifest)
    prompt_skeleton: str | None = None
    if prompt_entry is not None:
        raw = (
            prompt_entry.content
            if prompt_entry.content is not None
            else _read_template_file(
                template_dir,
                prompt_entry.source or "",
                manifest.name,  # source set (xor'd)
            )
        )
        prompt_skeleton = _render(
            raw,
            _preview_variables(manifest),
            source_desc=f"{manifest.name} prompt skeleton preview",
        )

    return TemplateInfo(
        name=manifest.name,
        description=manifest.description,
        path=str(template_dir.resolve()),
        source=source,
        params=params,
        required_agents=list(manifest.required_agents),
        prompt_skeleton=prompt_skeleton,
    )


# ---------------------------------------------------------------------------
# Discovery (HLD §2.1)
# ---------------------------------------------------------------------------


def _scan_template_dirs(
    root: Path, source: Literal["builtin", "workspace", "path"]
) -> list[TemplateInfo]:
    """Scan exactly one level of *root* for child dirs containing a template.yaml."""
    infos = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / "template.yaml").is_file():
            infos.append(_load_template_info(child, source))
    return infos


def discover_templates(workspace_root: str, config: ProjectConfig | None) -> list[TemplateInfo]:
    """Discover built-in + workspace-registered templates (HLD §2.1).

    Built-ins are loaded first, then workspace-registered entries are loaded and OVERWRITE
    any built-in of the same name (workspace shadows built-in, per HLD). The built-in root
    not existing yet is tolerated silently (package data landing separately/concurrently);
    a *configured* workspace `templates:` entry that doesn't exist on disk is a hard error
    (a workspace explicitly opted into that path -- silently ignoring a typo would hide a
    broken config, unlike the built-in-root case which is a packaging-timing concern, not
    a user misconfiguration).
    """
    found: dict[str, TemplateInfo] = {}

    if _BUILTIN_ROOT.is_dir():
        for info in _scan_template_dirs(_BUILTIN_ROOT, "builtin"):
            found[info.name] = info

    if config is not None:
        for entry in config.templates:
            entry_path = Path(entry)
            if not entry_path.is_dir():
                raise TemplateError(f"configured template path not found: {entry_path}")
            if (entry_path / "template.yaml").is_file():
                info = _load_template_info(entry_path, "workspace")
                found[info.name] = info
            else:
                for info in _scan_template_dirs(entry_path, "workspace"):
                    found[info.name] = info

    return sorted(found.values(), key=lambda t: t.name)


def load_template(
    path_or_name: str, workspace_root: str, config: ProjectConfig | None
) -> TemplateInfo:
    """Resolve *path_or_name* to a `TemplateInfo` (HLD §2.1 point 3 / §2.5).

    Accepts either a directory path containing `template.yaml` (ad-hoc, e.g.
    `ao new /path/to/template ...`) or the `name:` of a discovered built-in/workspace
    template.
    """
    candidate = Path(path_or_name)
    if candidate.is_dir() and (candidate / "template.yaml").is_file():
        return _load_template_info(candidate, "path")

    for info in discover_templates(workspace_root, config):
        if info.name == path_or_name:
            return info

    raise TemplateError(
        f"unknown template {path_or_name!r}: not a directory containing template.yaml, and "
        "no discovered built-in/workspace template has this name (see `ao templates`)"
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


# B2 fix: a bare JSON number (optional '-', digits, optional single '.') is substituted
# unescaped -- this is what lets a `.json` template splice a param straight into a NUMERIC
# position with no surrounding quotes (e.g. the built-in routed-runner's circuit-breaker
# budget thresholds, `"threshold": {{ params.task_budget_usd }}`) keep working.
_JSON_BARE_NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?$")


def _json_escape_value(value: str) -> str:
    """JSON-string-escape *value*'s characters for safe placement inside a JSON string
    literal a template already supplies the surrounding quotes for (B2 fix).

    Deliberately does NOT add quotes itself -- `json.dumps(value)[1:-1]` strips the pair
    `json.dumps` adds, leaving only the escaped characters -- so a value can never break
    out of the JSON string it's substituted into (a stray `"`, `\\`, or control character
    is escaped rather than terminating the string early). A bare-numeric value is left
    untouched so numeric substitutions still render as valid unquoted JSON numbers.
    """
    if _JSON_BARE_NUMBER_RE.match(value):
        return value
    return json.dumps(value)[1:-1]


def _render(
    text: str, variables: dict[str, str], *, source_desc: str, escape_json: bool = False
) -> str:
    """Whitespace-tolerant `{{ var }}` substitution (HLD §2.2). Unknown variable -> TemplateError
    naming the variable and the offending file/description (fail fast, no silent leakage).

    `escape_json=True` (set by callers rendering content into a `.json`-suffixed target,
    B2 fix) JSON-string-escapes every substituted value via `_json_escape_value` so a
    param value can never inject structure into the generated workflow spec.
    """

    def _sub(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in variables:
            raise TemplateError(f"unknown template variable '{{{{ {name} }}}}' in {source_desc}")
        value = variables[name]
        return _json_escape_value(value) if escape_json else value

    return _VAR_RE.sub(_sub, text)


# ---------------------------------------------------------------------------
# id/slug resolution (HLD §2.4 note)
# ---------------------------------------------------------------------------


def _compile_id_pattern(id_pattern: str) -> re.Pattern[str]:
    """Build a regex (with named groups `rand6`/`slug`) that matches ids produced by
    *id_pattern*, so a caller-supplied string can be tested as "already a full id"."""
    parts: list[str] = []
    last = 0
    for m in _ID_PATTERN_TOKEN_RE.finditer(id_pattern):
        parts.append(re.escape(id_pattern[last : m.start()]))
        token = m.group(1)
        if token == "rand6":
            parts.append(r"(?P<rand6>[a-z0-9]{6})")
        else:
            parts.append(r"(?P<slug>[a-z0-9][a-z0-9-]*)")
        last = m.end()
    parts.append(re.escape(id_pattern[last:]))
    return re.compile("^" + "".join(parts) + "$")


def _random_rand6() -> str:
    """6 random lowercase-alnum chars via the stdlib `random` module (not `secrets`):
    deliberately chosen so tests can get deterministic ids via `random.seed(N)` before
    calling `instantiate()` -- this is scaffold-time id generation, not orchestration-run
    logic on the engine's run path, so CLAUDE.md's injectable-RNG rule (which is scoped to
    "the run path") doesn't apply; module-global `random.seed()` is the simplest sufficient
    seam here, mirroring new-epic-run.sh's own random-suffix generation.
    """
    alphabet = string.ascii_lowercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(6))  # noqa: S311 (non-crypto id suffix)


def _resolve_id(id_pattern: str, slug_or_id: str) -> tuple[str, str]:
    """Return (id, slug). Accepts a full id matching *id_pattern*, or a bare slug (from
    which an id is generated, with `{rand6}` filled in if the pattern uses it)."""
    regex = _compile_id_pattern(id_pattern)
    m = regex.match(slug_or_id)
    if m:
        return slug_or_id, m.group("slug")

    if not _SLUG_RE.match(slug_or_id):
        raise TemplateError(
            f"{slug_or_id!r} is neither a valid id matching pattern {id_pattern!r} nor a bare "
            f"slug (must match {_SLUG_RE.pattern!r})"
        )
    slug = slug_or_id
    rand6 = _random_rand6() if "{rand6}" in id_pattern else ""
    new_id = id_pattern.replace("{rand6}", rand6).replace("{slug}", slug)
    return new_id, slug


# ---------------------------------------------------------------------------
# Param resolution
# ---------------------------------------------------------------------------


def _resolve_params(param_specs: dict[str, _ParamSpec], provided: dict[str, str]) -> dict[str, str]:
    unknown = sorted(set(provided) - set(param_specs))
    if unknown:
        raise TemplateError(
            f"unknown template param(s) {unknown} (declared params: {sorted(param_specs)})"
        )

    resolved: dict[str, str] = {}
    missing_required: list[str] = []
    for name, spec in param_specs.items():
        value = provided.get(name)
        if not value:  # None or empty string -> fall back to default
            value = spec.default
        if value is None:
            if spec.required:
                missing_required.append(name)
            value = ""
        elif spec.enum is not None and value not in spec.enum:
            raise TemplateError(f"param {name!r}: value {value!r} not in allowed enum {spec.enum}")
        resolved[name] = value

    if missing_required:
        raise TemplateError(f"missing required param(s): {missing_required}")
    return resolved


# ---------------------------------------------------------------------------
# instantiate() (HLD §2.4)
# ---------------------------------------------------------------------------


def _ws_resolve(store: LocalFsArtifactStore, rel_path: str, desc: str) -> Path:
    """Resolve *rel_path* against the workspace root, converting a path-escape into a
    TemplateError with context (layer-boundary wrapping, CLAUDE.md)."""
    try:
        return Path(store.resolve(rel_path))
    except ArtifactPathError as exc:
        raise TemplateError(f"{desc}: {exc}") from exc


def _workspace_rel(abs_path: Path, workspace_root: Path) -> str:
    return abs_path.relative_to(workspace_root).as_posix()


def _write_prompt_text(
    target_path: Path,
    text: str,
    *,
    created: list[str],
    skipped: list[str],
    workspace_root: Path,
) -> None:
    """Write caller-supplied prompt text, refusing to silently clobber a differing,
    already-materialized prompt.md (HLD §2.4 "instance-exists semantics" / the bash
    scaffolder's `cmp -s` pin-bug fix)."""
    if target_path.exists():
        existing = target_path.read_text(encoding="utf-8")
        if existing == text:
            skipped.append(_workspace_rel(target_path, workspace_root))
            return
        raise TemplateError(
            f"prompt conflict: {target_path} already exists with different content; "
            "resolve manually (refusing to overwrite an edited prompt)"
        )
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(text, encoding="utf-8")
    created.append(_workspace_rel(target_path, workspace_root))


def _materialize_asset_file(
    src_file: Path,
    dest_file: Path,
    keep_existing: bool,
    variables: dict[str, str],
    *,
    created: list[str],
    skipped: list[str],
    workspace_root: Path,
) -> None:
    if dest_file.exists() and keep_existing:
        skipped.append(_workspace_rel(dest_file, workspace_root))
        return
    raw = src_file.read_text(encoding="utf-8")
    rendered = _render(
        raw,
        variables,
        source_desc=str(src_file),
        escape_json=dest_file.suffix.lower() == ".json",
    )
    dest_file.parent.mkdir(parents=True, exist_ok=True)
    dest_file.write_text(rendered, encoding="utf-8")
    created.append(_workspace_rel(dest_file, workspace_root))


def _materialize_asset(
    asset: _AssetEntry,
    template_dir: Path,
    store: LocalFsArtifactStore,
    workspace_root: Path,
    variables: dict[str, str],
    *,
    created: list[str],
    skipped: list[str],
) -> None:
    source_path = _safe_join(template_dir, asset.source, f"assets[{asset.source!r}].source")
    target_rel = _render(asset.target, variables, source_desc=f"assets[{asset.source!r}].target")
    target_path = _ws_resolve(store, target_rel, f"assets[{asset.source!r}].target")

    if source_path.is_dir():
        template_dir_resolved = template_dir.resolve()
        for src_file in sorted(source_path.rglob("*")):
            if not src_file.is_file():
                continue
            # B1 defense-in-depth: `source_path` itself is contained (via `_safe_join`
            # above), but `rglob` follows symlinked subdirectories -- resolve() each
            # candidate file and re-check containment before reading it, so a symlink
            # planted inside the asset dir can't smuggle an outside file into the copy.
            resolved_src = src_file.resolve()
            if not resolved_src.is_relative_to(template_dir_resolved):
                raise TemplateError(
                    f"assets[{asset.source!r}]: {src_file} escapes the template "
                    "directory (symlink?)"
                )
            dest_file = target_path / src_file.relative_to(source_path)
            _materialize_asset_file(
                resolved_src,
                dest_file,
                asset.keep_existing,
                variables,
                created=created,
                skipped=skipped,
                workspace_root=workspace_root,
            )
    elif source_path.is_file():
        _materialize_asset_file(
            source_path,
            target_path,
            asset.keep_existing,
            variables,
            created=created,
            skipped=skipped,
            workspace_root=workspace_root,
        )
    else:
        raise TemplateError(f"asset source not found: {source_path}")


def _validate_rendered_workflow(
    instance_dir_abs: Path, manifest: _TemplateManifest, variables: dict[str, str]
) -> tuple[Path, dict]:
    """Sanity-check the rendered workflow (HLD §2.3): parses as JSON/YAML, has id/tasks,
    declares a non-empty prompt_path, AND (B2 fix) validates against the real
    `WorkflowSpec` pydantic model / JSON Schema via `spec.load_workflow` -- the same
    loader `ao validate`/`ao run` use, reused rather than re-implemented. Raises
    TemplateError otherwise."""
    candidate: Path | None = None
    for f in manifest.files:
        target_rel = _render(f.target, variables, source_desc=f"{manifest.name}.files.target")
        if Path(target_rel).name in _WORKFLOW_BASENAMES:
            candidate = instance_dir_abs / target_rel
            break

    if candidate is None or not candidate.exists():
        raise TemplateError(
            f"template {manifest.name!r} does not render one of {_WORKFLOW_BASENAMES} "
            "into instance_dir -- every template must produce a workflow spec (HLD §2.3)"
        )

    text = candidate.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text) if candidate.suffix in (".yaml", ".yml") else json.loads(text)
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        raise TemplateError(f"rendered workflow {candidate} is not valid JSON/YAML: {exc}") from exc

    if not isinstance(data, dict) or "id" not in data or "tasks" not in data:
        raise TemplateError(
            f"rendered workflow {candidate} must be an object with 'id' and 'tasks' fields"
        )
    if not data.get("prompt_path"):
        raise TemplateError(
            f"rendered workflow {candidate} must declare a non-empty 'prompt_path' "
            "(HLD §2.3 -- required for the UI/CLI prompt box to work)"
        )

    # B2(b): full structural/type validation (JSON Schema + WorkflowSpec construction),
    # on top of the shallow id/tasks/prompt_path shape check above. Closes the gap where
    # a schema-shaped-but-injected payload (B2's structural-injection primitive, e.g. an
    # extra top-level key or a spliced-in extra task) would otherwise sail through
    # `instantiate()` unnoticed -- `additionalProperties: false` in workflow.schema.json
    # rejects exactly that shape.
    try:
        load_workflow(candidate)
    except OrchestratorError as exc:
        raise TemplateError(
            f"rendered workflow {candidate} failed WorkflowSpec validation: {exc}"
        ) from exc

    return candidate, data


def instantiate(
    template: TemplateInfo,
    workspace_root: str,
    *,
    slug_or_id: str,
    params: dict[str, str],
    prompt_text: str | None = None,
) -> InstantiateResult:
    """Scaffold (or idempotently re-scaffold) a run instance from *template* (HLD §2.4).

    Idempotent like new-epic-run.sh: re-running on an existing id regenerates non-
    `keep_existing` files and keeps prompt/outputs. A `prompt_text` that conflicts with an
    existing, differing prompt.md is a TemplateError (no silent overwrite).
    """
    template_dir = Path(template.path)
    manifest = _load_manifest(template_dir)  # re-parsed; see module docstring for why

    workspace_root_path = Path(workspace_root).resolve()
    store = LocalFsArtifactStore(str(workspace_root_path))

    id_, slug = _resolve_id(manifest.id_pattern, slug_or_id)
    resolved_params = _resolve_params(manifest.params, params)

    instance_dir_rel = _INSTANCE_DIR_TOKEN_RE.sub(
        lambda m: id_ if m.group(1) == "id" else slug, manifest.instance_dir
    )
    instance_dir_abs = _ws_resolve(store, instance_dir_rel, "instance_dir")

    variables: dict[str, str] = {
        "id": id_,
        "slug": slug,
        "instance_dir": instance_dir_rel,
        "workspace_root": str(workspace_root_path),
        **{f"params.{k}": v for k, v in resolved_params.items()},
    }

    created: list[str] = []
    skipped: list[str] = []

    instance_dir_abs.mkdir(parents=True, exist_ok=True)

    # dirs: created inside instance_dir.
    for d in manifest.dirs:
        d_rendered = _render(d, variables, source_desc=f"{manifest.name}.dirs")
        dpath = _ws_resolve(
            store, posixpath.join(instance_dir_rel, d_rendered), f"{manifest.name}.dirs"
        )
        if not dpath.exists():
            dpath.mkdir(parents=True, exist_ok=True)
            created.append(_workspace_rel(dpath, workspace_root_path))

    prompt_entry = _find_prompt_entry(manifest)

    # files: rendered into instance_dir.
    for f in manifest.files:
        target_rel = _render(f.target, variables, source_desc=f"{manifest.name}.files.target")
        full_rel = posixpath.join(instance_dir_rel, target_rel)
        target_path = _ws_resolve(store, full_rel, f"files[{f.target!r}].target")

        if f.when is not None:
            pname = _strip_when_prefix(f.when)
            if not params.get(pname):  # truthy iff PROVIDED non-empty (not default-filled)
                if target_path.exists():
                    target_path.unlink()
                continue

        if f is prompt_entry and prompt_text is not None:
            _write_prompt_text(
                target_path,
                prompt_text,
                created=created,
                skipped=skipped,
                workspace_root=workspace_root_path,
            )
            continue

        if target_path.exists() and f.keep_existing:
            skipped.append(_workspace_rel(target_path, workspace_root_path))
            continue

        raw = (
            f.content
            if f.content is not None
            else _read_template_file(template_dir, f.source or "", manifest.name)
        )
        rendered = _render(
            raw,
            variables,
            source_desc=f.source or f"{manifest.name} ({f.target})",
            escape_json=target_path.suffix.lower() == ".json",
        )
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(rendered, encoding="utf-8")
        created.append(_workspace_rel(target_path, workspace_root_path))

    if prompt_text is not None and prompt_entry is None:
        raise TemplateError(
            f"template {manifest.name!r} declares no prompt.md file entry; cannot write prompt_text"
        )

    # assets: materialized once per workspace (keyed to workspace_root, not instance_dir).
    for a in manifest.assets:
        _materialize_asset(
            a,
            template_dir,
            store,
            workspace_root_path,
            variables,
            created=created,
            skipped=skipped,
        )

    workflow_path, workflow_data = _validate_rendered_workflow(
        instance_dir_abs, manifest, variables
    )

    prompt_path_rel = workflow_data.get("prompt_path")
    prompt_path_abs = (
        str(_ws_resolve(store, prompt_path_rel, "workflow.prompt_path"))
        if prompt_path_rel
        else None
    )

    return InstantiateResult(
        instance_dir=str(instance_dir_abs),
        workflow_path=str(workflow_path.resolve()),
        prompt_path=prompt_path_abs,
        created=created,
        skipped=skipped,
    )
