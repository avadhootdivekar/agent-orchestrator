"""Unit tests for `agent_orchestrator.templates` (E-Tpl3x9, HLD §2.2/§2.4).

Covers manifest validation, `{{ var }}` rendering, `instantiate()` semantics (idempotent
re-scaffold, `when`-gate deletion of a stale target, `keep_existing`, prompt-conflict
detection), id/slug resolution, and discovery precedence (workspace shadows built-in).
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest
import yaml

import agent_orchestrator.templates as templates_mod
from agent_orchestrator.project_config import ProjectConfig
from agent_orchestrator.templates import (
    InstantiateResult,
    TemplateError,
    TemplateInfo,
    discover_templates,
    instantiate,
    load_template,
)

# ---------------------------------------------------------------------------
# Fixture-template builder
# ---------------------------------------------------------------------------

_DEFAULT_MANIFEST: dict = {
    "version": "1.0",
    "name": "basic",
    "description": "A minimal test template.",
    "id_pattern": "e-{rand6}-{slug}",
    "instance_dir": "runs/{id}",
    "params": {
        "greeting": {"description": "Greeting text", "required": False, "default": "hello"},
    },
    "dirs": ["outputs"],
    "files": [
        {"source": "workflow.json.tmpl", "target": "workflow.json"},
        {"source": "prompt.md.tmpl", "target": "prompt.md", "keep_existing": True},
    ],
    "required_agents": ["worker"],
}

_DEFAULT_WORKFLOW: dict = {
    "version": "1.0",
    "id": "{{ id }}",
    "repo_set": "default-set",
    "prompt_path": "{{ instance_dir }}/prompt.md",
    "tasks": [
        {
            "id": "do-work",
            "agent": "worker",
            "instruction": "instructions/do-work.md",
            "inputs": ["{{ instance_dir }}/prompt.md"],
            "outputs": ["{{ instance_dir }}/outputs/result.md"],
            "depends_on": [],
        }
    ],
}


def _write_template(
    root: Path,
    *,
    dirname: str = "basic-template",
    manifest: dict | None = None,
    manifest_overrides: dict | None = None,
    workflow: dict | None = None,
    workflow_overrides: dict | None = None,
    prompt_tmpl: str = "# {{ id }}\n",
    extra_files: dict[str, str] | None = None,
    write_workflow_file: bool = True,
) -> Path:
    """Write a minimal template dir under *root* and return its path."""
    tdir = root / dirname
    tdir.mkdir(parents=True, exist_ok=True)

    m = dict(manifest) if manifest is not None else json.loads(json.dumps(_DEFAULT_MANIFEST))
    if manifest_overrides:
        m.update(manifest_overrides)
    (tdir / "template.yaml").write_text(yaml.safe_dump(m, sort_keys=False))

    if write_workflow_file:
        wf = dict(workflow) if workflow is not None else json.loads(json.dumps(_DEFAULT_WORKFLOW))
        if workflow_overrides:
            wf.update(workflow_overrides)
        (tdir / "workflow.json.tmpl").write_text(json.dumps(wf))

    (tdir / "prompt.md.tmpl").write_text(prompt_tmpl)

    for name, content in (extra_files or {}).items():
        path = tdir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    return tdir


def _load_info(tdir: Path, workspace: Path) -> TemplateInfo:
    return load_template(str(tdir), str(workspace), None)


# ---------------------------------------------------------------------------
# Manifest validation
# ---------------------------------------------------------------------------


class TestManifestValidation:
    def test_dir_without_manifest_is_not_treated_as_a_path_template(self, tmp_path: Path) -> None:
        """A directory lacking template.yaml is correctly NOT recognized as an ad-hoc path
        template (HLD §2.1 point 3); load_template falls back to a by-name lookup, which
        also fails since no such name is discovered either."""
        empty_dir = tmp_path / "no-manifest"
        empty_dir.mkdir()
        with pytest.raises(TemplateError, match="unknown template"):
            load_template(str(empty_dir), str(tmp_path), None)

    def test_missing_manifest_file_direct(self, tmp_path: Path) -> None:
        # _load_manifest is exercised directly (bypassing load_template's path-vs-name
        # branching above) so the "manifest not found" message itself is covered too.
        with pytest.raises(TemplateError, match="not found"):
            templates_mod._load_manifest(tmp_path / "no-manifest-here")

    def test_invalid_yaml_raises(self, tmp_path: Path) -> None:
        tdir = tmp_path / "bad-yaml"
        tdir.mkdir()
        (tdir / "template.yaml").write_text("name: [unterminated")
        with pytest.raises(TemplateError, match="invalid YAML"):
            load_template(str(tdir), str(tmp_path), None)

    def test_manifest_not_a_mapping_raises(self, tmp_path: Path) -> None:
        tdir = tmp_path / "not-a-map"
        tdir.mkdir()
        (tdir / "template.yaml").write_text("- a\n- b\n")
        with pytest.raises(TemplateError, match="mapping"):
            load_template(str(tdir), str(tmp_path), None)

    def test_missing_required_field_instance_dir(self, tmp_path: Path) -> None:
        m = dict(_DEFAULT_MANIFEST)
        del m["instance_dir"]
        tdir = _write_template(tmp_path, manifest=m)
        with pytest.raises(TemplateError, match="instance_dir"):
            load_template(str(tdir), str(tmp_path), None)

    def test_source_and_content_both_set_rejected(self, tmp_path: Path) -> None:
        m = dict(_DEFAULT_MANIFEST)
        m["files"] = [{"source": "workflow.json.tmpl", "content": "x", "target": "workflow.json"}]
        tdir = _write_template(tmp_path, manifest=m)
        with pytest.raises(TemplateError, match="exactly one"):
            load_template(str(tdir), str(tmp_path), None)

    def test_source_and_content_neither_set_rejected(self, tmp_path: Path) -> None:
        m = dict(_DEFAULT_MANIFEST)
        m["files"] = [{"target": "workflow.json"}]
        tdir = _write_template(tmp_path, manifest=m)
        with pytest.raises(TemplateError, match="exactly one"):
            load_template(str(tdir), str(tmp_path), None)

    def test_unknown_manifest_field_rejected(self, tmp_path: Path) -> None:
        m = dict(_DEFAULT_MANIFEST)
        m["totally_unknown_field"] = "x"
        tdir = _write_template(tmp_path, manifest=m)
        with pytest.raises(TemplateError):
            load_template(str(tdir), str(tmp_path), None)

    def test_when_references_undeclared_param_rejected(self, tmp_path: Path) -> None:
        m = dict(_DEFAULT_MANIFEST)
        m["files"] = [
            {"source": "workflow.json.tmpl", "target": "workflow.json"},
            {"content": "x", "target": "outputs/x.txt", "when": "params.nope"},
        ]
        tdir = _write_template(tmp_path, manifest=m)
        with pytest.raises(TemplateError, match="undeclared param"):
            load_template(str(tdir), str(tmp_path), None)

    def test_id_pattern_missing_slug_token_rejected(self, tmp_path: Path) -> None:
        m = dict(_DEFAULT_MANIFEST)
        m["id_pattern"] = "e-{rand6}"
        tdir = _write_template(tmp_path, manifest=m)
        with pytest.raises(TemplateError, match=r"\{slug\}"):
            load_template(str(tdir), str(tmp_path), None)

    def test_bad_template_name_rejected(self, tmp_path: Path) -> None:
        m = dict(_DEFAULT_MANIFEST)
        m["name"] = "Not_Valid Name"
        tdir = _write_template(tmp_path, manifest=m)
        with pytest.raises(TemplateError, match="name"):
            load_template(str(tdir), str(tmp_path), None)

    def test_traversal_in_file_target_rejected(self, tmp_path: Path) -> None:
        m = dict(_DEFAULT_MANIFEST)
        m["files"] = [{"source": "workflow.json.tmpl", "target": "../escape.json"}]
        tdir = _write_template(tmp_path, manifest=m)
        with pytest.raises(TemplateError, match=r"\.\."):
            load_template(str(tdir), str(tmp_path), None)

    def test_enum_default_mismatch_rejected(self, tmp_path: Path) -> None:
        m = dict(_DEFAULT_MANIFEST)
        m["params"] = {
            "type": {"enum": ["a", "b"], "default": "c"},
        }
        tdir = _write_template(tmp_path, manifest=m)
        with pytest.raises(TemplateError, match="enum"):
            load_template(str(tdir), str(tmp_path), None)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


class TestRendering:
    def test_unknown_variable_names_var_and_file(self, tmp_path: Path) -> None:
        tdir = _write_template(tmp_path, prompt_tmpl="hello {{ nope_this_var }}\n")
        # prompt_skeleton preview render happens at load time -- unknown var caught there,
        # with an error naming both the offending variable and a description of the file.
        with pytest.raises(TemplateError, match="nope_this_var") as excinfo:
            _load_info(tdir, tmp_path)
        assert "prompt" in str(excinfo.value)

    def test_unknown_variable_at_instantiate_time(self, tmp_path: Path) -> None:
        m = dict(_DEFAULT_MANIFEST)
        m["files"] = [
            {"source": "workflow.json.tmpl", "target": "workflow.json"},
            {"content": "{{ totally_bogus }}\n", "target": "outputs/x.txt"},
        ]
        tdir = _write_template(tmp_path, manifest=m)
        info = _load_info(tdir, tmp_path)
        with pytest.raises(TemplateError, match="totally_bogus"):
            instantiate(info, str(tmp_path), slug_or_id="my-slug", params={})

    def test_params_variable_renders_provided_value(self, tmp_path: Path) -> None:
        tdir = _write_template(tmp_path, prompt_tmpl="greeting={{ params.greeting }}\n")
        info = _load_info(tdir, tmp_path)
        result = instantiate(info, str(tmp_path), slug_or_id="x", params={"greeting": "howdy"})
        assert Path(result.prompt_path).read_text() == "greeting=howdy\n"

    def test_params_variable_falls_back_to_default(self, tmp_path: Path) -> None:
        tdir = _write_template(tmp_path, prompt_tmpl="greeting={{ params.greeting }}\n")
        info = _load_info(tdir, tmp_path)
        result = instantiate(info, str(tmp_path), slug_or_id="x", params={})
        assert Path(result.prompt_path).read_text() == "greeting=hello\n"  # manifest default

    def test_whitespace_tolerant_braces(self, tmp_path: Path) -> None:
        tdir = _write_template(tmp_path, prompt_tmpl="id={{id}} id2={{   id   }}\n")
        info = _load_info(tdir, tmp_path)
        result = instantiate(info, str(tmp_path), slug_or_id="my-slug", params={})
        prompt_text = (Path(result.instance_dir) / "prompt.md").read_text()
        assert "id=" in prompt_text
        # Both spacing variants resolved to the same (non-empty, non-templated) value.
        parts = prompt_text.strip().split()
        assert parts[0].split("=")[1] == parts[1].split("=")[1]
        assert "{{" not in prompt_text


# ---------------------------------------------------------------------------
# instantiate()
# ---------------------------------------------------------------------------


class TestInstantiateBasics:
    def test_creates_dirs_files_and_result_paths(self, tmp_path: Path) -> None:
        tdir = _write_template(tmp_path)
        info = _load_info(tdir, tmp_path)
        result = instantiate(info, str(tmp_path), slug_or_id="my-thing", params={})

        assert isinstance(result, InstantiateResult)
        instance_dir = Path(result.instance_dir)
        assert instance_dir.is_dir()
        assert (instance_dir / "outputs").is_dir()
        assert (instance_dir / "workflow.json").is_file()
        assert (instance_dir / "prompt.md").is_file()
        assert result.workflow_path == str(instance_dir / "workflow.json")
        assert result.prompt_path == str(instance_dir / "prompt.md")

        wf = json.loads((instance_dir / "workflow.json").read_text())
        assert wf["id"].startswith("e-")
        assert wf["id"].endswith("-my-thing")
        assert wf["prompt_path"] == f"runs/{wf['id']}/prompt.md"

    def test_created_and_skipped_are_workspace_relative(self, tmp_path: Path) -> None:
        tdir = _write_template(tmp_path)
        info = _load_info(tdir, tmp_path)
        result = instantiate(info, str(tmp_path), slug_or_id="my-thing", params={})
        for rel in result.created:
            assert not Path(rel).is_absolute()
            assert (tmp_path / rel).exists()

    def test_idempotent_reinstantiate(self, tmp_path: Path) -> None:
        tdir = _write_template(tmp_path)
        info = _load_info(tdir, tmp_path)
        r1 = instantiate(info, str(tmp_path), slug_or_id="my-thing", params={})
        full_id = Path(r1.instance_dir).name

        r2 = instantiate(info, str(tmp_path), slug_or_id=full_id, params={})

        # prompt.md (keep_existing) untouched second time -> reported as skipped.
        assert any(p.endswith("prompt.md") for p in r2.skipped)
        # workflow.json has no keep_existing -> regenerated (still reported as created).
        assert any(p.endswith("workflow.json") for p in r2.created)
        # Directory not recreated / re-reported.
        assert not any(p.endswith("/outputs") for p in r2.created)

    def test_missing_required_param_raises(self, tmp_path: Path) -> None:
        m = dict(_DEFAULT_MANIFEST)
        m["params"] = {"must_have": {"required": True}}
        tdir = _write_template(tmp_path, manifest=m)
        info = _load_info(tdir, tmp_path)
        with pytest.raises(TemplateError, match="missing required"):
            instantiate(info, str(tmp_path), slug_or_id="x", params={})

    def test_missing_required_param_satisfied_by_default_is_ok(self, tmp_path: Path) -> None:
        m = dict(_DEFAULT_MANIFEST)
        m["params"] = {"has_default": {"required": True, "default": "d"}}
        tdir = _write_template(tmp_path, manifest=m)
        info = _load_info(tdir, tmp_path)
        # Should not raise -- default satisfies "required".
        instantiate(info, str(tmp_path), slug_or_id="x", params={})

    def test_unknown_param_raises(self, tmp_path: Path) -> None:
        tdir = _write_template(tmp_path)
        info = _load_info(tdir, tmp_path)
        with pytest.raises(TemplateError, match="unknown template param"):
            instantiate(info, str(tmp_path), slug_or_id="x", params={"bogus": "y"})

    def test_enum_violation_raises(self, tmp_path: Path) -> None:
        m = dict(_DEFAULT_MANIFEST)
        m["params"] = {"type": {"enum": ["a", "b"]}}
        tdir = _write_template(tmp_path, manifest=m)
        info = _load_info(tdir, tmp_path)
        with pytest.raises(TemplateError, match="enum"):
            instantiate(info, str(tmp_path), slug_or_id="x", params={"type": "c"})


class TestWhenGateAndKeepExisting:
    def _template_with_when(self, tmp_path: Path) -> Path:
        m = dict(_DEFAULT_MANIFEST)
        m["params"] = {"type": {"required": False}}
        m["files"] = [
            {"source": "workflow.json.tmpl", "target": "workflow.json"},
            {"content": "{{ params.type }}\n", "target": "outputs/forced-type.txt", "when": "type"},
        ]
        return _write_template(tmp_path, manifest=m)

    def test_when_true_writes_file(self, tmp_path: Path) -> None:
        tdir = self._template_with_when(tmp_path)
        info = _load_info(tdir, tmp_path)
        result = instantiate(info, str(tmp_path), slug_or_id="x", params={"type": "bug"})
        target = Path(result.instance_dir) / "outputs" / "forced-type.txt"
        assert target.is_file()
        assert target.read_text().strip() == "bug"

    def test_when_false_deletes_stale_target(self, tmp_path: Path) -> None:
        """Pin-bug regression: a stale forced-type-style file from a PRIOR instantiate()
        call (where the param WAS provided) must be deleted on a later call where the param
        is absent -- it must not silently keep pinning the old value."""
        tdir = self._template_with_when(tmp_path)
        info = _load_info(tdir, tmp_path)
        r1 = instantiate(info, str(tmp_path), slug_or_id="x", params={"type": "bug"})
        target = Path(r1.instance_dir) / "outputs" / "forced-type.txt"
        assert target.is_file()

        full_id = Path(r1.instance_dir).name
        instantiate(info, str(tmp_path), slug_or_id=full_id, params={})
        assert not target.exists()

    def test_when_accepts_bare_and_prefixed_param_name(self, tmp_path: Path) -> None:
        """`when: type` (bare) and `when: params.type` (HLD §2.2-documented form) are
        equivalent -- the shipped built-in routed-runner template uses the bare form."""
        m = dict(_DEFAULT_MANIFEST)
        m["params"] = {"type": {"required": False}}
        m["files"] = [
            {"source": "workflow.json.tmpl", "target": "workflow.json"},
            {"content": "x\n", "target": "outputs/bare.txt", "when": "type"},
            {"content": "x\n", "target": "outputs/prefixed.txt", "when": "params.type"},
        ]
        tdir = _write_template(tmp_path, manifest=m)
        info = _load_info(tdir, tmp_path)
        result = instantiate(info, str(tmp_path), slug_or_id="x", params={"type": "bug"})
        instance_dir = Path(result.instance_dir)
        assert (instance_dir / "outputs" / "bare.txt").is_file()
        assert (instance_dir / "outputs" / "prefixed.txt").is_file()

    def test_keep_existing_preserves_user_edits(self, tmp_path: Path) -> None:
        tdir = _write_template(tmp_path)
        info = _load_info(tdir, tmp_path)
        r1 = instantiate(info, str(tmp_path), slug_or_id="x", params={})
        prompt_path = Path(r1.instance_dir) / "prompt.md"
        prompt_path.write_text("hand-edited content\n")

        full_id = Path(r1.instance_dir).name
        r2 = instantiate(info, str(tmp_path), slug_or_id=full_id, params={})

        assert prompt_path.read_text() == "hand-edited content\n"
        assert any(p.endswith("prompt.md") for p in r2.skipped)


class TestPromptText:
    def test_prompt_text_written_when_absent(self, tmp_path: Path) -> None:
        tdir = _write_template(tmp_path)
        info = _load_info(tdir, tmp_path)
        result = instantiate(
            info, str(tmp_path), slug_or_id="x", params={}, prompt_text="my custom prompt\n"
        )
        assert Path(result.prompt_path).read_text() == "my custom prompt\n"
        assert any(p.endswith("prompt.md") for p in result.created)

    def test_prompt_text_idempotent_when_identical(self, tmp_path: Path) -> None:
        tdir = _write_template(tmp_path)
        info = _load_info(tdir, tmp_path)
        r1 = instantiate(info, str(tmp_path), slug_or_id="x", params={}, prompt_text="same text\n")
        full_id = Path(r1.instance_dir).name
        r2 = instantiate(
            info, str(tmp_path), slug_or_id=full_id, params={}, prompt_text="same text\n"
        )
        assert any(p.endswith("prompt.md") for p in r2.skipped)

    def test_prompt_text_conflict_raises(self, tmp_path: Path) -> None:
        tdir = _write_template(tmp_path)
        info = _load_info(tdir, tmp_path)
        r1 = instantiate(
            info, str(tmp_path), slug_or_id="x", params={}, prompt_text="first version\n"
        )
        full_id = Path(r1.instance_dir).name
        with pytest.raises(TemplateError, match="conflict"):
            instantiate(
                info,
                str(tmp_path),
                slug_or_id=full_id,
                params={},
                prompt_text="a DIFFERENT version\n",
            )

    def test_prompt_text_without_prompt_entry_raises(self, tmp_path: Path) -> None:
        m = dict(_DEFAULT_MANIFEST)
        m["files"] = [{"source": "workflow.json.tmpl", "target": "workflow.json"}]
        tdir = _write_template(tmp_path, manifest=m)
        info = _load_info(tdir, tmp_path)
        with pytest.raises(TemplateError, match="prompt.md"):
            instantiate(info, str(tmp_path), slug_or_id="x", params={}, prompt_text="hi\n")


class TestSanityCheckRenderedWorkflow:
    def test_no_workflow_file_raises(self, tmp_path: Path) -> None:
        m = dict(_DEFAULT_MANIFEST)
        m["files"] = [{"source": "prompt.md.tmpl", "target": "prompt.md"}]
        tdir = _write_template(tmp_path, manifest=m)
        info = _load_info(tdir, tmp_path)
        with pytest.raises(TemplateError, match="workflow"):
            instantiate(info, str(tmp_path), slug_or_id="x", params={})

    def test_missing_id_or_tasks_raises(self, tmp_path: Path) -> None:
        wf = {
            "version": "1.0",
            "repo_set": "default-set",
            "prompt_path": "{{ instance_dir }}/prompt.md",
        }
        tdir = _write_template(tmp_path, workflow=wf)
        info = _load_info(tdir, tmp_path)
        with pytest.raises(TemplateError, match="id.*tasks|tasks.*id"):
            instantiate(info, str(tmp_path), slug_or_id="x", params={})

    def test_missing_prompt_path_raises(self, tmp_path: Path) -> None:
        wf = dict(_DEFAULT_WORKFLOW)
        del wf["prompt_path"]
        tdir = _write_template(tmp_path, workflow=wf)
        info = _load_info(tdir, tmp_path)
        with pytest.raises(TemplateError, match="prompt_path"):
            instantiate(info, str(tmp_path), slug_or_id="x", params={})

    def test_invalid_json_raises(self, tmp_path: Path) -> None:
        tdir = _write_template(tmp_path, write_workflow_file=False)
        (tdir / "workflow.json.tmpl").write_text("{not valid json")
        info = _load_info(tdir, tmp_path)
        with pytest.raises(TemplateError, match="not valid"):
            instantiate(info, str(tmp_path), slug_or_id="x", params={})


class TestAssets:
    def test_asset_dir_materialized_once_and_keep_existing_respected(self, tmp_path: Path) -> None:
        m = dict(_DEFAULT_MANIFEST)
        m["assets"] = [
            {"source": "instructions/", "target": "shared/instructions/", "keep_existing": True}
        ]
        tdir = _write_template(
            tmp_path,
            manifest=m,
            extra_files={"instructions/a.md": "Hello {{ id }}\n", "instructions/b.md": "B\n"},
        )
        info = _load_info(tdir, tmp_path)

        r1 = instantiate(info, str(tmp_path), slug_or_id="first", params={})
        shared_a = tmp_path / "shared" / "instructions" / "a.md"
        assert shared_a.is_file()
        assert "Hello" in shared_a.read_text()
        assert any(p.endswith("instructions/a.md") for p in r1.created)

        # Simulate a workspace tune-up of the shared asset.
        shared_a.write_text("TUNED BY WORKSPACE\n")

        # A second instance must NOT clobber the tuned asset (keep_existing).
        r2 = instantiate(info, str(tmp_path), slug_or_id="second", params={})
        assert shared_a.read_text() == "TUNED BY WORKSPACE\n"
        assert any(p.endswith("instructions/a.md") for p in r2.skipped)

    def test_asset_source_missing_raises(self, tmp_path: Path) -> None:
        m = dict(_DEFAULT_MANIFEST)
        m["assets"] = [{"source": "does-not-exist/", "target": "shared/"}]
        tdir = _write_template(tmp_path, manifest=m)
        info = _load_info(tdir, tmp_path)
        with pytest.raises(TemplateError, match="not found"):
            instantiate(info, str(tmp_path), slug_or_id="x", params={})


class TestPathSafety:
    def test_param_value_cannot_escape_workspace_via_rendered_target(self, tmp_path: Path) -> None:
        m = dict(_DEFAULT_MANIFEST)
        m["params"] = {"evil": {"required": True}}
        m["files"] = [
            {"source": "workflow.json.tmpl", "target": "workflow.json"},
            {"content": "x\n", "target": "{{ params.evil }}"},
        ]
        tdir = _write_template(tmp_path, manifest=m)
        info = _load_info(tdir, tmp_path)
        with pytest.raises(TemplateError, match="escapes|workspace"):
            instantiate(
                info,
                str(tmp_path),
                slug_or_id="x",
                params={"evil": "../../../../etc/passwd"},
            )

    # -- B1: absolute `source:` rejected at manifest-validation time --------------

    def test_absolute_source_in_files_rejected_at_manifest_time(self, tmp_path: Path) -> None:
        """review B1: `Path(template_dir) / "/etc/passwd"` silently discards template_dir
        (absolute right operand wins), so an absolute `files[].source` must be rejected
        loudly at manifest-validation time, before any file IO happens."""
        m = dict(_DEFAULT_MANIFEST)
        m["files"] = [{"source": "/etc/passwd", "target": "workflow.json"}]
        tdir = _write_template(tmp_path, manifest=m)
        with pytest.raises(TemplateError, match="absolute"):
            load_template(str(tdir), str(tmp_path), None)

    def test_absolute_source_in_assets_rejected_at_manifest_time(self, tmp_path: Path) -> None:
        m = dict(_DEFAULT_MANIFEST)
        m["assets"] = [{"source": "/etc", "target": "shared/"}]
        tdir = _write_template(tmp_path, manifest=m)
        with pytest.raises(TemplateError, match="absolute"):
            load_template(str(tdir), str(tmp_path), None)

    # -- B1: read-site defense-in-depth beyond the literal '..' segment check -----

    def test_symlink_escape_blocked_at_read_site_for_file_source(self, tmp_path: Path) -> None:
        """A symlink INSIDE the template dir pointing outside it contains no literal '..'
        segment at all, so only the read-site resolve()+containment check (`_safe_join`),
        not the manifest-time string check, can catch it."""
        outside_dir = tmp_path / "outside-secret"
        outside_dir.mkdir()
        (outside_dir / "secret.txt").write_text("TOP SECRET\n")

        m = dict(_DEFAULT_MANIFEST)
        m["files"] = [
            {"source": "workflow.json.tmpl", "target": "workflow.json"},
            {"source": "escape-link/secret.txt", "target": "outputs/leaked.txt"},
        ]
        tdir = _write_template(tmp_path, manifest=m)
        (tdir / "escape-link").symlink_to(outside_dir, target_is_directory=True)
        info = _load_info(tdir, tmp_path)

        with pytest.raises(TemplateError, match="escapes"):
            instantiate(info, str(tmp_path), slug_or_id="x", params={})

        runs_dir = tmp_path / "runs"
        assert not runs_dir.exists() or not any(runs_dir.rglob("leaked.txt"))

    def test_symlink_escape_blocked_in_asset_directory_copy(self, tmp_path: Path) -> None:
        """review B1's second reproduction: an `assets[]` directory copy must not follow a
        symlink inside it out of the template dir (e.g. standing in for `~/.ssh`). Uses a
        symlink to a FILE (not a directory) -- `Path.rglob` on this Python version does not
        descend INTO a symlinked subdirectory, but it does yield a top-level symlink-to-file
        entry (and `.is_file()` follows it), so that is the exploitable shape."""
        outside_dir = tmp_path / "outside-secret-dir"
        outside_dir.mkdir()
        secret_file = outside_dir / "id_rsa"
        secret_file.write_text("PRIVATE KEY\n")

        m = dict(_DEFAULT_MANIFEST)
        m["assets"] = [{"source": "instructions/", "target": "shared/instructions/"}]
        tdir = _write_template(
            tmp_path,
            manifest=m,
            extra_files={"instructions/a.md": "safe\n"},
        )
        (tdir / "instructions" / "escape-link").symlink_to(secret_file)
        info = _load_info(tdir, tmp_path)

        with pytest.raises(TemplateError, match="escapes"):
            instantiate(info, str(tmp_path), slug_or_id="x", params={})

        shared_dir = tmp_path / "shared"
        assert not shared_dir.exists() or not any(shared_dir.rglob("id_rsa"))


# ---------------------------------------------------------------------------
# B2: JSON structural injection via {{ params.* }} substitution
# ---------------------------------------------------------------------------


class TestJsonEscaping:
    def _template_with_templated_repo_set(self, tmp_path: Path) -> Path:
        m = dict(_DEFAULT_MANIFEST)
        m["params"] = {"repo_set": {"description": "repo set", "required": True}}
        wf = dict(_DEFAULT_WORKFLOW)
        wf["repo_set"] = "{{ params.repo_set }}"
        return _write_template(tmp_path, manifest=m, workflow=wf)

    def test_quote_breakout_payload_renders_as_inert_string(self, tmp_path: Path) -> None:
        """review's exact reproduction payload: a `repo_set` value containing a raw `"`
        must no longer break out of its JSON string context -- it must render as an inert
        string value, with no extra top-level key injected."""
        tdir = self._template_with_templated_repo_set(tmp_path)
        info = _load_info(tdir, tmp_path)
        payload = 'fin-plan", "injected_key": "pwned'

        result = instantiate(info, str(tmp_path), slug_or_id="x", params={"repo_set": payload})

        wf = json.loads(Path(result.workflow_path).read_text())
        assert wf["repo_set"] == payload
        assert "injected_key" not in wf

    def test_backslash_and_control_chars_also_escaped(self, tmp_path: Path) -> None:
        tdir = self._template_with_templated_repo_set(tmp_path)
        info = _load_info(tdir, tmp_path)
        payload = 'a\\b"c\nd'

        result = instantiate(info, str(tmp_path), slug_or_id="x", params={"repo_set": payload})

        wf = json.loads(Path(result.workflow_path).read_text())
        assert wf["repo_set"] == payload

    def test_rendered_workflow_failing_full_spec_validation_raises(self, tmp_path: Path) -> None:
        """B2(b): `_validate_rendered_workflow` now runs the real `WorkflowSpec` pydantic
        model (via `spec.load_workflow`), not just the shallow id/tasks/prompt_path shape
        check -- a workflow missing a schema-required field must be rejected."""
        wf = dict(_DEFAULT_WORKFLOW)
        del wf["version"]  # required by workflow.schema.json / WorkflowSpec
        tdir = _write_template(tmp_path, workflow=wf)
        info = _load_info(tdir, tmp_path)
        with pytest.raises(TemplateError, match="WorkflowSpec validation"):
            instantiate(info, str(tmp_path), slug_or_id="x", params={})

    def test_builtin_routed_runner_budget_thresholds_render_as_bare_json_numbers(
        self, tmp_path: Path
    ) -> None:
        """B2(a)'s explicit non-regression: the shipped `routed-runner` built-in splices
        `{{ params.task_budget_usd }}` / `{{ params.run_budget_usd }}` directly into an
        UNQUOTED numeric JSON position -- these must render as bare JSON numbers, not be
        wrapped in quotes or otherwise mangled by the new JSON-escaping."""
        info = load_template("routed-runner", str(tmp_path), None)
        result = instantiate(
            info, str(tmp_path), slug_or_id="my-run", params={"repo_set": "fin-plan"}
        )
        wf = json.loads(Path(result.workflow_path).read_text())
        breakers = {b["id"]: b for b in wf["circuit_breakers"]}
        assert breakers["task-budget-cap"]["threshold"] == 75
        assert breakers["run-budget-cap"]["threshold"] == 1500
        assert isinstance(breakers["task-budget-cap"]["threshold"], int | float)


# ---------------------------------------------------------------------------
# id/slug resolution
# ---------------------------------------------------------------------------


class TestIdSlugResolution:
    def test_bare_slug_generates_id_with_seeded_rand6(self, tmp_path: Path) -> None:
        tdir = _write_template(tmp_path)
        info = _load_info(tdir, tmp_path)

        random.seed(42)
        r1 = instantiate(info, str(tmp_path), slug_or_id="my-feature", params={})
        id1 = Path(r1.instance_dir).name

        random.seed(42)
        r2 = instantiate(info, str(tmp_path / "ws2"), slug_or_id="my-feature", params={})
        id2 = Path(r2.instance_dir).name

        assert id1 == id2  # same seed -> same rand6 -> same id
        assert id1.startswith("e-")
        assert id1.endswith("-my-feature")

    def test_full_id_accepted_and_slug_extracted(self, tmp_path: Path) -> None:
        # A dedicated prompt template that renders `{{ slug }}` in isolation (not embedded
        # inside `{{ id }}`) so the extracted slug value itself can be asserted precisely.
        tdir = _write_template(tmp_path, prompt_tmpl="slug={{ slug }}\n")
        info = _load_info(tdir, tmp_path)
        result = instantiate(
            info, str(tmp_path), slug_or_id="e-abc123-my-existing-feature", params={}
        )
        assert Path(result.instance_dir).name == "e-abc123-my-existing-feature"
        assert Path(result.prompt_path).read_text() == "slug=my-existing-feature\n"
        wf = json.loads(Path(result.workflow_path).read_text())
        assert wf["id"] == "e-abc123-my-existing-feature"

    def test_invalid_slug_or_id_raises(self, tmp_path: Path) -> None:
        tdir = _write_template(tmp_path)
        info = _load_info(tdir, tmp_path)
        with pytest.raises(TemplateError, match="neither a valid id"):
            instantiate(info, str(tmp_path), slug_or_id="Not Valid!!", params={})

    def test_custom_id_pattern_without_rand6(self, tmp_path: Path) -> None:
        m = dict(_DEFAULT_MANIFEST)
        m["id_pattern"] = "custom-{slug}"
        tdir = _write_template(tmp_path, manifest=m)
        info = _load_info(tdir, tmp_path)
        result = instantiate(info, str(tmp_path), slug_or_id="thing", params={})
        assert Path(result.instance_dir).name == "custom-thing"


# ---------------------------------------------------------------------------
# Discovery precedence
# ---------------------------------------------------------------------------


class TestDiscovery:
    @pytest.fixture(autouse=True)
    def _isolate_builtin_root(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """Point the module's built-in root at an empty tmp dir by default, so these tests
        never depend on (or are polluted by) the real shipped built-in templates."""
        fake_builtin_root = tmp_path / "fake-builtin-root"
        monkeypatch.setattr(templates_mod, "_BUILTIN_ROOT", fake_builtin_root)
        return fake_builtin_root

    def test_missing_builtin_root_tolerated(self, tmp_path: Path) -> None:
        assert discover_templates(str(tmp_path), None) == []

    def test_discovers_workspace_template_dir_entry(self, tmp_path: Path) -> None:
        tdir = _write_template(tmp_path / "cfg-templates", dirname="basic-template")
        cfg = ProjectConfig(templates=[str(tdir)])
        infos = discover_templates(str(tmp_path), cfg)
        assert [i.name for i in infos] == ["basic"]
        assert infos[0].source == "workspace"

    def test_workspace_shadows_builtin_of_same_name(
        self, tmp_path: Path, _isolate_builtin_root: Path
    ) -> None:
        builtin_root = _isolate_builtin_root
        builtin_root.mkdir(parents=True)
        _write_template(
            builtin_root,
            dirname="basic",
            manifest_overrides={"description": "BUILTIN VERSION"},
        )
        ws_tdir = _write_template(
            tmp_path / "ws",
            dirname="basic",
            manifest_overrides={"description": "WORKSPACE VERSION"},
        )
        cfg = ProjectConfig(templates=[str(ws_tdir)])

        infos = discover_templates(str(tmp_path), cfg)
        assert len(infos) == 1
        assert infos[0].source == "workspace"
        assert infos[0].description == "WORKSPACE VERSION"

    def test_dir_of_template_dirs_scanned_one_level(self, tmp_path: Path) -> None:
        container = tmp_path / "many-templates"
        _write_template(container, dirname="tmpl-a", manifest_overrides={"name": "tmpl-a"})
        _write_template(container, dirname="tmpl-b", manifest_overrides={"name": "tmpl-b"})
        cfg = ProjectConfig(templates=[str(container)])
        infos = discover_templates(str(tmp_path), cfg)
        assert sorted(i.name for i in infos) == ["tmpl-a", "tmpl-b"]

    def test_configured_path_not_found_raises(self, tmp_path: Path) -> None:
        cfg = ProjectConfig(templates=[str(tmp_path / "does-not-exist")])
        with pytest.raises(TemplateError, match="not found"):
            discover_templates(str(tmp_path), cfg)

    def test_load_template_by_name(self, tmp_path: Path) -> None:
        tdir = _write_template(tmp_path / "cfg-templates", dirname="basic-template")
        cfg = ProjectConfig(templates=[str(tdir)])
        info = load_template("basic", str(tmp_path), cfg)
        assert info.name == "basic"

    def test_load_template_by_path(self, tmp_path: Path) -> None:
        tdir = _write_template(tmp_path)
        info = load_template(str(tdir), str(tmp_path), None)
        assert info.source == "path"

    def test_load_template_unknown_name_raises(self, tmp_path: Path) -> None:
        with pytest.raises(TemplateError, match="unknown template"):
            load_template("does-not-exist", str(tmp_path), None)
