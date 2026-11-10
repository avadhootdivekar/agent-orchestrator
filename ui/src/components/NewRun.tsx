import { useEffect, useState } from "react";
import { api } from "../api";
import { errorMessage } from "../errors";
import type { RunOptions, WorkflowInfo } from "../types";
import { useLaunchPanel } from "../useLaunchPanel";
import { ErrorBanner } from "./common";
import { LaunchPending, LaunchResultPanel } from "./LaunchResultPanel";
import { optionErrors, RunOptionsForm, useRunOptionsSchema } from "./RunOptionsForm";
import { TemplateLaunch } from "./TemplateLaunch";

type Mode = "workflow" | "template";

/**
 * Launcher for a new run (FR-R1, FR-6).
 *
 * Two modes, toggled by tab: "From workflow" starts an already-scaffolded spec (original
 * behavior, unchanged below); "From template" (HLD §2.6/§2.7) scaffolds a brand-new
 * instance from a registered template first — see TemplateLaunch.
 *
 * The prompt text box maps to `ao run --prompt`: the server writes it into the selected
 * workflow's declared `prompt_path` before starting. A workflow that declares no
 * `prompt_path` has nowhere to put it, so the box is disabled and says why rather than
 * accepting text that would be silently dropped.
 *
 * Launching never navigates by itself: the outcome shows in a LaunchResultPanel and the
 * operator picks where to go (`onLaunched` fires only from an explicit button). `null` means
 * "go to the run list".
 */
export function NewRun({ onLaunched }: { onLaunched: (runId: string | null) => void }) {
  const [mode, setMode] = useState<Mode>("workflow");
  const [workflows, setWorkflows] = useState<WorkflowInfo[]>([]);
  const [workflowPath, setWorkflowPath] = useState("");
  const [prompt, setPrompt] = useState("");
  const [options, setOptions] = useState<RunOptions>({});
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const panel = useLaunchPanel("workflow");
  const schema = useRunOptionsSchema();
  const invalidOptions = Object.keys(optionErrors(schema, options)).length > 0;

  useEffect(() => {
    api
      .workflows()
      .then((list) => {
        setWorkflows(list);
        if (list.length > 0) setWorkflowPath(list[0].path);
      })
      .catch((err) => setError(errorMessage(err)));
  }, []);

  const selected = workflows.find((w) => w.path === workflowPath);
  const promptable = Boolean(selected?.prompt_path);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    panel.clear();
    try {
      panel.show(await api.startRun(workflowPath, prompt, options));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div>
      <div className="page-head">
        <h1>New run</h1>
      </div>

      <div className="tabs" role="tablist" aria-label="New run mode">
        <button
          type="button"
          role="tab"
          className="tab"
          aria-selected={mode === "workflow"}
          onClick={() => setMode("workflow")}
        >
          From workflow
        </button>
        <button
          type="button"
          role="tab"
          className="tab"
          aria-selected={mode === "template"}
          onClick={() => setMode("template")}
        >
          From template
        </button>
      </div>

      {mode === "template" ? <TemplateLaunch onLaunched={onLaunched} /> : null}

      {mode === "workflow" ? (
        <>
          <ErrorBanner message={error} />

          {submitting ? <LaunchPending /> : null}
          {!submitting && panel.launch ? (
            <LaunchResultPanel
              key={panel.launch.launch_id}
              initial={panel.launch}
              onRecord={panel.track}
              onOpenRun={(runId) => onLaunched(runId)}
              onOpenRunList={() => onLaunched(null)}
              onStartAnother={() => {
                setPrompt("");
                panel.clear();
              }}
              onEditRetry={panel.clear}
            />
          ) : null}

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
                    Written to <code className="mono">{selected?.prompt_path}</code> before the
                    run starts — the same as <code className="mono">ao run --prompt</code>.
                  </>
                ) : (
                  <>
                    Add <code className="mono">"prompt_path"</code> to the workflow spec and
                    list that path in a task&apos;s inputs to enable this.
                  </>
                )}
              </div>
            </div>

            <h2>Overrides</h2>
            <RunOptionsForm schema={schema} value={options} onChange={setOptions} />

            <div className="row" style={{ marginTop: 20 }}>
              <button className="primary" type="submit" disabled={submitting || !workflowPath || invalidOptions}>
                {submitting ? "Starting…" : "Start run"}
              </button>
              <span className="muted" style={{ fontSize: 12 }}>
                Runs as a separate <code className="mono">ao run</code> process — closing this
                tab will not stop it.
              </span>
            </div>
          </form>
        </>
      ) : null}
    </div>
  );
}
