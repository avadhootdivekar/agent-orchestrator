import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type {
  CreateInstanceRequest,
  CreateInstanceResponse,
  TemplateInfo,
  TemplateParam,
} from "../types";
import { Empty, ErrorBanner } from "./common";

/**
 * Launcher for a new run scaffolded from a workflow template ("From template" mode;
 * FR-6, HLD §2.6/§2.7).
 *
 * Unlike "From workflow", the workflow spec does not exist yet: picking a template and
 * filling params/prompt calls `POST /api/templates/{name}/instances`, which renders the
 * instance (workflow.json + prompt.md + aux files) server-side. "Create" only renders it
 * (`start: false`); "Create & run" also starts it (`start: true`) and hands off through
 * the same `onLaunched` callback RunDetail navigation already uses for the classic flow.
 */

/** Required params with no non-empty value — the client-side half of enforcement. */
export function missingRequiredParams(
  params: TemplateParam[],
  values: Record<string, string>,
): TemplateParam[] {
  return params.filter((param) => param.required && !(values[param.name] ?? "").trim());
}

/**
 * Shapes the POST body from form state. Blank param values are omitted (rather than sent
 * as `""`) so the server applies its own declared default instead of an explicit empty
 * override; an empty slug is likewise omitted so the server derives one from the prompt.
 */
export function buildCreateInstanceRequest({
  slug,
  paramValues,
  prompt,
  start,
}: {
  slug: string;
  paramValues: Record<string, string>;
  prompt: string;
  start: boolean;
}): CreateInstanceRequest {
  const params: Record<string, string> = {};
  for (const [name, value] of Object.entries(paramValues)) {
    if (value.trim() !== "") params[name] = value;
  }
  const body: CreateInstanceRequest = { params, prompt, start, options: {} };
  if (slug.trim()) body.slug_or_id = slug.trim();
  return body;
}

/** Initial param values for a freshly-selected template: declared defaults, else blank. */
function defaultParamValues(params: TemplateParam[]): Record<string, string> {
  const values: Record<string, string> = {};
  for (const param of params) {
    if (param.default) values[param.name] = param.default;
  }
  return values;
}

export function TemplateLaunch({
  onLaunched,
}: {
  onLaunched: (runId: string | null) => void;
}) {
  const [templates, setTemplates] = useState<TemplateInfo[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [templateName, setTemplateName] = useState("");
  const [paramValues, setParamValues] = useState<Record<string, string>>({});
  const [prompt, setPrompt] = useState("");
  const [slug, setSlug] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState<"create" | "run" | null>(null);
  const [created, setCreated] = useState<CreateInstanceResponse | null>(null);

  useEffect(() => {
    api
      .templates()
      .then((list) => {
        setTemplates(list);
        if (list.length > 0) setTemplateName(list[0].name);
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : String(err)))
      .finally(() => setLoaded(true));
  }, []);

  const selected = templates.find((template) => template.name === templateName);

  // Switching templates invalidates the previous template's param values/prompt/slug —
  // carrying them over would silently submit stale data against the newly-picked template.
  useEffect(() => {
    setParamValues(defaultParamValues(selected?.params ?? []));
    setPrompt(selected?.prompt_skeleton ?? "");
    setSlug("");
    setCreated(null);
    // Only the template identity should reset the form; `selected` is derived from it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [templateName]);

  const setParam = (name: string, value: string) => {
    setParamValues((prev) => ({ ...prev, [name]: value }));
  };

  const submit = async (start: boolean) => {
    if (!selected) return;
    const missing = missingRequiredParams(selected.params, paramValues);
    if (missing.length > 0) {
      setError(
        `Missing required parameter${missing.length === 1 ? "" : "s"}: ${missing
          .map((param) => param.name)
          .join(", ")}`,
      );
      return;
    }
    setSubmitting(start ? "run" : "create");
    setError(null);
    setCreated(null);
    try {
      const body = buildCreateInstanceRequest({ slug, paramValues, prompt, start });
      const response = await api.createInstance(selected.name, body);
      if (start) {
        onLaunched(response.launch?.run_id ?? null);
      } else {
        setCreated(response);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSubmitting(null);
    }
  };

  if (loaded && templates.length === 0) {
    return (
      <div>
        <ErrorBanner message={error} />
        <div className="card">
          <Empty>
            No templates registered. Add one under <code className="mono">templates:</code>{" "}
            in <code className="mono">.ao/config.yaml</code>, or use a built-in template
            shipped with agent-orchestrator.
          </Empty>
        </div>
      </div>
    );
  }

  return (
    <div>
      <ErrorBanner message={error} />

      <div className="card">
        <div className="field">
          <label htmlFor="template">Template</label>
          <select
            id="template"
            value={templateName}
            onChange={(event) => setTemplateName(event.target.value)}
          >
            {templates.length === 0 ? <option value="">Loading…</option> : null}
            {templates.map((template) => (
              <option key={template.name} value={template.name}>
                {template.name} — {template.description}
              </option>
            ))}
          </select>
          {selected ? (
            <div className="field-hint mono">
              {selected.path} · {selected.source}
            </div>
          ) : null}
        </div>

        {selected && selected.params.length > 0 ? (
          <>
            <h2>Parameters</h2>
            {selected.params.map((param) => (
              <div className="field" key={param.name}>
                <label htmlFor={`param-${param.name}`}>
                  {param.name}
                  {param.required ? " *" : ""}
                </label>
                {param.enum ? (
                  <select
                    id={`param-${param.name}`}
                    value={paramValues[param.name] ?? ""}
                    onChange={(event) => setParam(param.name, event.target.value)}
                  >
                    <option value="">{param.required ? "select…" : "unset"}</option>
                    {param.enum.map((choice) => (
                      <option key={choice} value={choice}>
                        {choice}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    id={`param-${param.name}`}
                    value={paramValues[param.name] ?? ""}
                    placeholder={param.default ?? ""}
                    onChange={(event) => setParam(param.name, event.target.value)}
                  />
                )}
                {param.description ? (
                  <div className="field-hint">{param.description}</div>
                ) : null}
              </div>
            ))}
          </>
        ) : null}

        <div className="field">
          <label htmlFor="template-prompt">Prompt</label>
          <textarea
            id="template-prompt"
            value={prompt}
            placeholder="Describe what this run should do…"
            onChange={(event) => setPrompt(event.target.value)}
          />
          <div className="field-hint">Written into the new instance&apos;s prompt.md.</div>
        </div>

        <div className="field">
          <label htmlFor="slug">Slug / id (optional)</label>
          <input
            id="slug"
            value={slug}
            placeholder="auto-derived from the first prompt line if left blank"
            onChange={(event) => setSlug(event.target.value)}
          />
        </div>

        {created ? (
          <div className="banner info">
            Created at <code className="mono">{created.instance_dir}</code> — workflow spec{" "}
            <code className="mono">{created.workflow_path}</code>. It now appears under{" "}
            <strong>From workflow</strong>, ready to review and start.
          </div>
        ) : null}

        <div className="row" style={{ marginTop: 20 }}>
          <button
            type="button"
            disabled={!selected || submitting !== null}
            onClick={() => void submit(false)}
          >
            {submitting === "create" ? "Creating…" : "Create"}
          </button>
          <button
            type="button"
            className="primary"
            disabled={!selected || submitting !== null}
            onClick={() => void submit(true)}
          >
            {submitting === "run" ? "Starting…" : "Create & run"}
          </button>
          <span className="muted" style={{ fontSize: 12 }}>
            Runs as a separate <code className="mono">ao run</code> process, same as From
            workflow.
          </span>
        </div>
      </div>
    </div>
  );
}
