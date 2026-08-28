import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { RunOptions, WorkflowInfo } from "../types";
import { ErrorBanner } from "./common";

/**
 * Launcher for a new run (FR-R1).
 *
 * The prompt text box maps to `ao run --prompt`: the server writes it into the selected
 * workflow's declared `prompt_path` before starting. A workflow that declares no
 * `prompt_path` has nowhere to put it, so the box is disabled and says why rather than
 * accepting text that would be silently dropped.
 */
export function NewRun({ onLaunched }: { onLaunched: (runId: string | null) => void }) {
  const [workflows, setWorkflows] = useState<WorkflowInfo[]>([]);
  const [workflowPath, setWorkflowPath] = useState("");
  const [prompt, setPrompt] = useState("");
  const [options, setOptions] = useState<RunOptions>({});
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    api
      .workflows()
      .then((list) => {
        setWorkflows(list);
        if (list.length > 0) setWorkflowPath(list[0].path);
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : String(err)));
  }, []);

  const selected = workflows.find((w) => w.path === workflowPath);
  const promptable = Boolean(selected?.prompt_path);

  const setOption = (key: keyof RunOptions, raw: string) => {
    setOptions((prev) => {
      const next = { ...prev };
      if (raw === "") {
        delete next[key];
        return next;
      }
      // Numeric knobs must go over the wire as numbers; the server allow-lists keys but
      // does not coerce types.
      const numeric = ["max_attempts", "max_turns", "max_parallel", "budget_total"];
      (next as Record<string, unknown>)[key] = numeric.includes(key) ? Number(raw) : raw;
      return next;
    });
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const record = await api.startRun(workflowPath, prompt, options);
      setPrompt("");
      onLaunched(record.run_id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div>
      <div className="page-head">
        <h1>New run</h1>
      </div>

      <ErrorBanner message={error} />

      <form className="card" onSubmit={submit}>
        <div className="field">
          <label htmlFor="workflow">Workflow</label>
          <select
            id="workflow"
            value={workflowPath}
            onChange={(event) => setWorkflowPath(event.target.value)}
          >
            {workflows.length === 0 ? <option value="">No workflows found</option> : null}
            {workflows.map((workflow) => (
              <option key={workflow.path} value={workflow.path}>
                {workflow.id} — {workflow.task_count} task
                {workflow.task_count === 1 ? "" : "s"}
              </option>
            ))}
          </select>
          {selected ? <div className="field-hint mono">{selected.path}</div> : null}
        </div>

        <div className="field">
          <label htmlFor="prompt">Prompt</label>
          <textarea
            id="prompt"
            value={prompt}
            disabled={!promptable}
            placeholder={
              promptable
                ? "Describe what this run should do…"
                : "This workflow declares no prompt_path, so it takes no prompt."
            }
            onChange={(event) => setPrompt(event.target.value)}
          />
          <div className="field-hint">
            {promptable ? (
              <>
                Written to <code className="mono">{selected?.prompt_path}</code> before the run
                starts — the same as <code className="mono">ao run --prompt</code>.
              </>
            ) : (
              <>
                Add <code className="mono">"prompt_path"</code> to the workflow spec and list
                that path in a task&apos;s inputs to enable this.
              </>
            )}
          </div>
        </div>

        <h2>Overrides</h2>
        <div className="options-grid">
          <div>
            <label htmlFor="model">Model</label>
            <input
              id="model"
              placeholder="default"
              onChange={(event) => setOption("model", event.target.value)}
            />
          </div>
          <div>
            <label htmlFor="effort">Effort</label>
            <select id="effort" onChange={(event) => setOption("effort", event.target.value)}>
              <option value="">default</option>
              <option value="low">low</option>
              <option value="medium">medium</option>
              <option value="high">high</option>
            </select>
          </div>
          <div>
            <label htmlFor="max_parallel">Max parallel</label>
            <input
              id="max_parallel"
              type="number"
              min={1}
              placeholder="1"
              onChange={(event) => setOption("max_parallel", event.target.value)}
            />
          </div>
          <div>
            <label htmlFor="max_attempts">Max attempts</label>
            <input
              id="max_attempts"
              type="number"
              min={1}
              placeholder="spec default"
              onChange={(event) => setOption("max_attempts", event.target.value)}
            />
          </div>
          <div>
            <label htmlFor="budget_total">Token budget</label>
            <input
              id="budget_total"
              type="number"
              min={1}
              placeholder="unlimited"
              onChange={(event) => setOption("budget_total", event.target.value)}
            />
          </div>
        </div>

        <div className="row" style={{ marginTop: 20 }}>
          <button className="primary" type="submit" disabled={submitting || !workflowPath}>
            {submitting ? "Starting…" : "Start run"}
          </button>
          <span className="muted" style={{ fontSize: 12 }}>
            Runs as a separate <code className="mono">ao run</code> process — closing this tab
            will not stop it.
          </span>
        </div>
      </form>
    </div>
  );
}
