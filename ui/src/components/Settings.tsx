import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { GeneralInstruction, WorkspaceInfo } from "../types";
import { Empty, ErrorBanner } from "./common";

/**
 * Read-only view of the workspace configuration, including general instructions.
 *
 * General instructions are the "define once per workspace, applied to every task" layer.
 * Showing whether each path actually **exists** is the point of this view: the engine drops
 * an unresolvable general instruction with a log warning and carries on, so a typo is
 * otherwise silent — every task quietly runs without the house rules the operator believes
 * are in force.
 */
export function Settings() {
  const [workspace, setWorkspace] = useState<WorkspaceInfo | null>(null);
  const [instructions, setInstructions] = useState<GeneralInstruction[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.workspace(), api.generalInstructions()])
      .then(([info, list]) => {
        setWorkspace(info);
        setInstructions(list);
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : String(err)));
  }, []);

  const missing = instructions.filter((instruction) => !instruction.exists);

  return (
    <div className="stack">
      <div>
        <div className="page-head">
          <h1>Workspace</h1>
        </div>

        <ErrorBanner message={error} />

        <div className="card">
          <Row label="Workspace root" value={workspace?.workspace_root} />
          <Row label="Config file" value={workspace?.config_path ?? "none found"} />
          <Row label="Workflow" value={workspace?.workflow ?? "not configured"} />
          <Row label="Reposets" value={workspace?.reposets ?? "not configured"} />
          <Row label="Agents" value={workspace?.agents ?? "not configured"} />
        </div>
      </div>

      <div>
        <h2>General instructions</h2>
        <p className="secondary" style={{ marginTop: 0, fontSize: 13 }}>
          Applied to <strong>every task of every run</strong> in this workspace, in addition to
          each task&apos;s own instruction. Configure them once in{" "}
          <code className="mono">.ao/config.yaml</code> under{" "}
          <code className="mono">general_instructions:</code>, or via{" "}
          <code className="mono">AO_GENERAL_INSTRUCTIONS</code>. A workflow may add its own.
        </p>

        {missing.length > 0 ? (
          <div className="banner error">
            {missing.length} configured general instruction
            {missing.length === 1 ? "" : "s"} could not be found on disk. The engine skips
            unresolvable paths, so those rules are <strong>not</strong> reaching your tasks.
          </div>
        ) : null}

        {instructions.length === 0 ? (
          <div className="card">
            <Empty>
              None configured. Add{" "}
              <code className="mono">general_instructions: [path/to/rules.md]</code> to{" "}
              <code className="mono">.ao/config.yaml</code>.
            </Empty>
          </div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Path</th>
                  <th>Resolved</th>
                  <th>Found</th>
                </tr>
              </thead>
              <tbody>
                {instructions.map((instruction) => (
                  <tr key={instruction.resolved}>
                    <td className="mono">{instruction.path}</td>
                    <td className="mono muted" style={{ fontSize: 11 }}>
                      {instruction.resolved}
                    </td>
                    <td>
                      <span className={`chip ${instruction.exists ? "good" : "critical"}`}>
                        <span className="chip-glyph" aria-hidden="true">
                          {instruction.exists ? "✓" : "✕"}
                        </span>
                        {instruction.exists ? "found" : "missing"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div className="row" style={{ padding: "6px 0", gap: 12, alignItems: "baseline" }}>
      <span className="secondary" style={{ minWidth: 130, fontSize: 12 }}>
        {label}
      </span>
      <span className="mono" style={{ wordBreak: "break-all" }}>
        {value ?? "—"}
      </span>
    </div>
  );
}
