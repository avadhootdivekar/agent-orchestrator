import { useCallback, useMemo, useState } from "react";
import { api, ApiError } from "../api";
import { Empty, ErrorBanner } from "../components/common";
import { TaskDetailPanel } from "../graph/TaskDetailPanel";
import type { RunDetail, RunGraph } from "../types";
import { POLL_MS, usePolling } from "../usePolling";
import { useTabActions } from "./context";
import { TabLink } from "./TabLink";

/**
 * A task as a full tab (E-iafh2F FR-8, kind `task`): reuses the graph's detail panel
 * (`TaskDetailPanel`) fed by the run detail + graph payloads, polled while the tab is active.
 */
export function TaskTab({ runId, taskId }: { runId: string; taskId: string }) {
  const actions = useTabActions();
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [graph, setGraph] = useState<RunGraph | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [d, g] = await Promise.all([api.run(runId), api.runGraph(runId)]);
      setDetail(d);
      setGraph(g);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }, [runId]);
  usePolling(refresh, POLL_MS);

  const statsById = useMemo(
    () => new Map((detail?.tasks ?? []).map((task) => [task.id, task])),
    [detail],
  );

  return (
    <div className="task-tab stack">
      <div className="page-head">
        <h1 className="mono">{taskId}</h1>
        <div className="row" style={{ gap: 12 }}>
          <TabLink target={{ kind: "run", params: { id: runId } }} className="link">
            ← Run {runId}
          </TabLink>
          <TabLink target={{ kind: "graph", params: { run: runId } }} className="link">
            Graph
          </TabLink>
        </div>
      </div>
      <ErrorBanner message={error} />
      {graph && detail ? (
        <TaskDetailPanel
          nodeId={taskId}
          graph={graph}
          statsById={statsById}
          runId={runId}
          onClose={() => actions.navigate({ kind: "run", params: { id: runId } })}
          onNavigate={(id) => actions.navigate({ kind: "task", params: { run: runId, id } })}
        />
      ) : !error ? (
        <Empty>Loading task…</Empty>
      ) : null}
    </div>
  );
}
