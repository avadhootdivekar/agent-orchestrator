"""Shared prompt assembly for executors (E-Ui7Kq2 FR-GI1).

Every executor that renders an agent prompt goes through :func:`build_prompt`, so the
"general instructions reach every task" guarantee is enforced in ONE place rather than
re-implemented (and eventually forgotten) per executor.

NFR-1 invariant holds here as everywhere else: this module interpolates **paths and ids
only** and never opens an instruction, input, or artifact file.
"""

from __future__ import annotations

from ..models import TaskContext

# Template placeholder an agent's `prompt_template` may use to position the general
# instructions itself. When a template does NOT reference it, `build_prompt` appends the
# fallback clause below instead — see `_GENERAL_INSTRUCTIONS_CLAUSE`.
GENERAL_INSTRUCTIONS_FIELD = "general_instructions"

# Appended to the rendered prompt when general instructions are configured but the agent's
# own `prompt_template` does not mention `{general_instructions}`.
#
# Why append rather than rely solely on the placeholder: the user-facing contract is that
# general instructions apply to EVERY task "regardless". Every agents.json written before
# this feature existed — and the AgentSpec.prompt_template default — has no placeholder, so
# a placeholder-only design would silently drop the workspace's instructions for exactly
# the configs most likely to be in use. Templates that DO position the placeholder keep
# full control and get no duplicate clause.
_GENERAL_INSTRUCTIONS_CLAUSE = (
    " Also follow the general instructions that apply to every task in this workspace: {paths}."
)


def render_general_instructions(paths: list[str]) -> str:
    """Render *paths* as the space-separated path list used in prompts.

    Empty list renders as the empty string, which is what keeps a workspace with no
    general instructions byte-identical to its pre-feature prompts.
    """
    return " ".join(paths)


def build_prompt(ctx: TaskContext) -> str:
    """Render the agent prompt for *ctx* from its agent's ``prompt_template``.

    The template's own placeholders are filled first. Then, if the task has general
    instructions configured and the template did not itself reference
    ``{general_instructions}``, a trailing clause naming those paths is appended so the
    guarantee holds for every agent config, including ones written before the feature
    existed.

    Returns:
        The fully rendered prompt string (paths/ids only — NFR-1).
    """
    template = ctx.agent.prompt_template
    general = render_general_instructions(ctx.general_instruction_paths)

    prompt = template.format(
        instruction=ctx.instruction_path,
        inputs=" ".join(ctx.input_paths),
        outputs=" ".join(ctx.output_paths),
        repos=" ".join(f"{k}={v}" for k, v in ctx.repo_paths.items()),
        dynamic_inputs=" ".join(ctx.dynamic_input_paths),
        output_manifest=ctx.output_manifest_path or "",
        **{GENERAL_INSTRUCTIONS_FIELD: general},
    )

    placeholder = "{" + GENERAL_INSTRUCTIONS_FIELD + "}"
    if general and placeholder not in template:
        prompt += _GENERAL_INSTRUCTIONS_CLAUSE.format(paths=general)

    return prompt
