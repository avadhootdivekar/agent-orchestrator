import { FileBrowser } from "../components/FileBrowser";
import { NewRun } from "../components/NewRun";
import { RunDetail } from "../components/RunDetail";
import { RunsList } from "../components/RunsList";
import { Settings } from "../components/Settings";
import { Usage } from "../components/Usage";
import { useTabActions } from "./context";
import { GraphTab } from "./GraphTab";
import type { Tab } from "./model";
import { TaskTab } from "./TaskTab";

/**
 * Maps a validated {@link Tab} to the existing view component. Params were allowlisted by
 * `model.ts` before they got here; the components pass them to the typed API client only.
 */
export function TabView({ tab }: { tab: Tab }) {
  const actions = useTabActions();
  const goRun = (id: string) => actions.navigate({ kind: "run", params: { id } });
  const goRuns = () => actions.navigate({ kind: "runs", params: {} });

  switch (tab.kind) {
    case "runs":
      return <RunsList onOpen={goRun} />;
    case "run":
      return <RunDetail runId={tab.params.id} onBack={goRuns} />;
    case "task":
      return <TaskTab runId={tab.params.run} taskId={tab.params.id} />;
    case "graph":
      return <GraphTab runId={tab.params.run} />;
    case "file":
      return (
        <FileBrowser
          initialPath={tab.params.path}
          initialRoot={tab.params.root}
          onFileOpened={(path) =>
            actions.retarget(tab.id, {
              kind: "file",
              params: { path, ...(tab.params.root ? { root: tab.params.root } : {}) },
            })
          }
        />
      );
    case "usage":
      return <Usage />;
    case "new":
      return <NewRun onLaunched={(runId) => (runId ? goRun(runId) : goRuns())} />;
    case "settings":
      return <Settings />;
  }
}
