import { lazy, Suspense, useCallback, useState } from "react";
import { api, ApiError } from "../api";
import { Empty, ErrorBanner } from "../components/common";
import type { RunDetail } from "../types";
import { POLL_MS, usePolling } from "../usePolling";
import { TabLink } from "./TabLink";

// Same lazy chunk RunDetail uses: React Flow/dagre load only when a graph is first shown.
const RunGraph = lazy(() => import("../graph/RunGraph"));

/** A run's dependency/spawn graph as its own tab (E-iafh2F, kind `graph`). */
export function GraphTab({ runId }: { runId: string }) {
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setDetail(await api.run(runId));
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }, [runId]);
  usePolling(refresh, POLL_MS);

  return (
    <div className="stack">
      <div className="page-head">
        <h1 className="mono">Graph · {runId}</h1>
        <TabLink target={{ kind: "run", params: { id: runId } }} className="link">
          ← Run {runId}
        </TabLink>
      </div>
      <ErrorBanner message={error} />
      {detail?.graph_version ? (
        <Suspense fallback={<Empty>Loading graph…</Empty>}>
          <RunGraph runId={runId} tasks={detail.tasks} graphVersion={detail.graph_version} />
        </Suspense>
      ) : detail ? (
        <Empty>This backend does not provide a run graph.</Empty>
      ) : !error ? (
        <Empty>Loading…</Empty>
      ) : null}
    </div>
  );
}
