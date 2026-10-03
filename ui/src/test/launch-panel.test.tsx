import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FailedLaunches } from "../components/FailedLaunches";
import {
  LaunchPending,
  LaunchResultPanel,
} from "../components/LaunchResultPanel";
import { NewRun } from "../components/NewRun";
import { RunsList } from "../components/RunsList";
import { TemplateLaunch } from "../components/TemplateLaunch";
import {
  dismissLaunch,
  isWaiting,
  launchStatusOf,
  readDismissedLaunches,
  readStoredLaunchId,
  writeStoredLaunchId,
} from "../launch";
import { TabActionsContext } from "../tabs/context";
import type { LaunchRecord, TemplateInfo, WorkflowInfo } from "../types";

const BASE: LaunchRecord = {
  launch_id: "launch-20261003T120000000000Z",
  kind: "run",
  pid: 1,
  argv: ["ao", "run"],
  started_at: "2026-10-03T12:00:00+00:00",
  log_path: "/ws/.orchestrator/ui/logs/x.log",
  run_id: null,
  workflow_path: "/ws/wf.json",
  prompt_chars: 0,
  finished_at: null,
  exit_code: null,
  cancelled: false,
};
const FAILED: LaunchRecord = {
  ...BASE,
  status: "failed_to_start",
  exit_code: 1,
  finished_at: "2026-10-03T12:00:02+00:00",
  log_tail: "ERROR: Unknown repo_set: ai-models\n<b>not html</b>",
};
const STARTED: LaunchRecord = {
  ...BASE,
  status: "started",
  run_id: "wf-20261003T120001Z",
};
const STARTING: LaunchRecord = { ...BASE, status: "starting" };
const UNCONFIRMED: LaunchRecord = { ...BASE, status: "running_unconfirmed" };

const WORKFLOWS: WorkflowInfo[] = [
  {
    id: "wf",
    name: "wf",
    path: "/ws/wf.json",
    task_count: 1,
    prompt_path: "prompt.md",
    general_instructions: [],
    error: null,
  },
];
const TEMPLATES: TemplateInfo[] = [
  {
    name: "tpl",
    description: "d",
    path: "/t",
    source: "builtin",
    params: [
      {
        name: "repo_set",
        description: "",
        required: true,
        enum: ["fin-plan"],
        default: null,
      },
    ],
    required_agents: [],
    prompt_skeleton: "skeleton",
  },
];

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Route by method+prefix; `post` produces the launch POST response. */
function stubApi(opts: {
  post?: () => Response;
  get?: Record<string, () => Response>;
}) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push(`${init?.method ?? "GET"} ${url}`);
      if (init?.method === "POST")
        return opts.post ? opts.post() : json({}, 500);
      const key = Object.keys(opts.get ?? {}).find((r) => url.startsWith(r));
      return key ? opts.get![key]() : json({}, 404);
    }),
  );
  return calls;
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function panelProps(
  overrides: Partial<Parameters<typeof LaunchResultPanel>[0]> = {},
) {
  return {
    initial: FAILED,
    onOpenRun: vi.fn(),
    onOpenRunList: vi.fn(),
    onStartAnother: vi.fn(),
    onEditRetry: vi.fn(),
    ...overrides,
  };
}

describe("launch helpers", () => {
  it("prefers the server status and falls back for an older backend", () => {
    expect(launchStatusOf(FAILED)).toBe("failed_to_start");
    expect(launchStatusOf({ ...BASE, run_id: "r" })).toBe("started");
    expect(launchStatusOf({ ...BASE, exit_code: 1 })).toBe("failed_to_start");
    expect(launchStatusOf({ ...BASE, finished_at: "x" })).toBe(
      "failed_to_start",
    );
    expect(launchStatusOf(BASE)).toBe("starting");
    expect(isWaiting("starting") && isWaiting("running_unconfirmed")).toBe(
      true,
    );
    expect(isWaiting("started") || isWaiting("failed_to_start")).toBe(false);
  });

  it("stores/clears the last launch id and tolerates broken storage", () => {
    writeStoredLaunchId("a", "id1");
    expect(readStoredLaunchId("a")).toBe("id1");
    writeStoredLaunchId("a", null);
    expect(readStoredLaunchId("a")).toBeNull();
    const boom = vi
      .spyOn(Storage.prototype, "getItem")
      .mockImplementation(() => {
        throw new Error("blocked");
      });
    expect(readStoredLaunchId("a")).toBeNull();
    expect(readDismissedLaunches()).toEqual([]);
    boom.mockRestore();
    const set = vi
      .spyOn(Storage.prototype, "setItem")
      .mockImplementation(() => {
        throw new Error("blocked");
      });
    expect(() => writeStoredLaunchId("a", "x")).not.toThrow();
    expect(dismissLaunch("z")).toEqual(["z"]);
    set.mockRestore();
  });

  it("dismissals are de-duplicated, bounded and ignore junk", () => {
    localStorage.setItem("ao.launch.dismissed", "{not json");
    expect(readDismissedLaunches()).toEqual([]);
    localStorage.setItem("ao.launch.dismissed", JSON.stringify({ a: 1 }));
    expect(readDismissedLaunches()).toEqual([]);
    localStorage.setItem("ao.launch.dismissed", JSON.stringify(["a", 3, "b"]));
    expect(readDismissedLaunches()).toEqual(["a", "b"]);
    expect(dismissLaunch("a")).toEqual(["b", "a"]);
    for (let i = 0; i < 250; i++) dismissLaunch(`id${i}`);
    expect(readDismissedLaunches()).toHaveLength(200);
    expect(readDismissedLaunches().at(-1)).toBe("id249");
  });
});

describe("LaunchPending", () => {
  it("is a polite status region", () => {
    render(<LaunchPending />);
    expect(screen.getByRole("status")).toHaveTextContent("Starting…");
  });
});

describe("LaunchResultPanel states", () => {
  it("Run started: shows the id, navigates only on explicit clicks", async () => {
    const props = panelProps({ initial: STARTED });
    render(<LaunchResultPanel {...props} />);
    expect(screen.getByRole("status")).toHaveTextContent("Run started");
    expect(screen.getByText("wf-20261003T120001Z")).toBeInTheDocument();
    expect(props.onOpenRun).not.toHaveBeenCalled();
    // Outside the workspace shell there is no new-tab affordance.
    expect(
      screen.queryByRole("button", { name: "Open in new tab" }),
    ).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Open run" }));
    expect(props.onOpenRun).toHaveBeenCalledWith("wf-20261003T120001Z");
    await userEvent.click(
      screen.getByRole("button", { name: "Start another" }),
    );
    expect(props.onStartAnother).toHaveBeenCalledTimes(1);
  });

  it("Run started: Open in new tab uses the tab mechanism, not onOpenRun", async () => {
    const open = vi.fn();
    const props = panelProps({ initial: STARTED });
    render(
      <TabActionsContext.Provider
        value={{ available: true, navigate: vi.fn(), open }}
      >
        <LaunchResultPanel {...props} />
      </TabActionsContext.Provider>,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Open in new tab" }),
    );
    expect(open).toHaveBeenCalledWith(
      { kind: "run", params: { id: "wf-20261003T120001Z" } },
      { activate: true },
    );
    expect(props.onOpenRun).not.toHaveBeenCalled();
  });

  it("Failed to start: alert with exit code, workflow and log as inert text", async () => {
    const props = panelProps({
      initial: { ...FAILED, log_truncated: true },
      retryHint: "Hint!",
    });
    render(<LaunchResultPanel {...props} />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Failed to start");
    expect(alert).toHaveTextContent("/ws/wf.json");
    expect(alert).toHaveTextContent("Earlier output omitted");
    expect(alert).toHaveTextContent("Hint!");
    const log = screen.getByLabelText("Launch log (last lines)");
    expect(log.tagName).toBe("PRE");
    expect(log).toHaveTextContent("ERROR: Unknown repo_set: ai-models");
    // Rendered as text: the markup in the log is NOT parsed.
    expect(log.querySelector("b")).toBeNull();
    expect(log).toHaveTextContent("<b>not html</b>");

    expect(props.onOpenRun).not.toHaveBeenCalled();
    expect(props.onOpenRunList).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: /Edit & retry/ }));
    expect(props.onEditRetry).toHaveBeenCalledTimes(1);
    await userEvent.click(
      screen.getByRole("button", { name: "Open run list" }),
    );
    expect(props.onOpenRunList).toHaveBeenCalledTimes(1);
  });

  it("Failed to start: Copy log writes the log to the clipboard, and reports failure", async () => {
    const writeText = vi
      .fn()
      .mockResolvedValueOnce(undefined)
      .mockRejectedValueOnce(new Error("no"));
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    render(<LaunchResultPanel {...panelProps()} />);
    await userEvent.click(screen.getByRole("button", { name: "Copy log" }));
    expect(writeText).toHaveBeenCalledWith(FAILED.log_tail);
    expect(await screen.findByText("Log copied")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Copy log" }));
    expect(await screen.findByText("Copy failed")).toBeInTheDocument();
  });

  it("Failed to start: unknown exit code and empty log read sensibly", () => {
    render(
      <LaunchResultPanel
        {...panelProps({
          initial: {
            ...FAILED,
            exit_code: null,
            workflow_path: null,
            log_tail: "",
          },
        })}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("exit code unknown");
    expect(screen.getByRole("alert")).toHaveTextContent(
      "(from project config)",
    );
    expect(screen.getByLabelText("Launch log (last lines)")).toHaveTextContent(
      "(the process printed nothing)",
    );
    expect(screen.getByRole("button", { name: "Copy log" })).toBeDisabled();
  });

  it("Failed to start without a log tail fetches it once", async () => {
    const calls = stubApi({
      get: {
        "/api/launches/": () => json({ ...FAILED, log_tail: "fetched line" }),
      },
    });
    const { log_tail: _drop, ...noLog } = FAILED;
    render(<LaunchResultPanel {...panelProps({ initial: noLog })} />);
    expect(screen.getByLabelText("Launch log (last lines)")).toHaveTextContent(
      "Loading log…",
    );
    await waitFor(() =>
      expect(
        screen.getByLabelText("Launch log (last lines)"),
      ).toHaveTextContent("fetched line"),
    );
    expect(calls).toHaveLength(1);
  });

  it("Failed to start: log fetch failure degrades to an empty log", async () => {
    stubApi({});
    const { log_tail: _drop, ...noLog } = FAILED;
    render(<LaunchResultPanel {...panelProps({ initial: noLog })} />);
    await waitFor(() =>
      expect(
        screen.getByLabelText("Launch log (last lines)"),
      ).toHaveTextContent("(the process printed nothing)"),
    );
  });
});

describe("LaunchResultPanel waiting", () => {
  it("polls the launch record, then flips to Run started without navigating", async () => {
    let n = 0;
    const calls = stubApi({
      get: { "/api/launches/": () => json(++n < 2 ? STARTING : STARTED) },
    });
    const props = panelProps({ initial: STARTING, pollMs: 10 });
    const onRecord = vi.fn();
    render(<LaunchResultPanel {...props} onRecord={onRecord} />);
    expect(screen.getByRole("status")).toHaveTextContent(
      "Starting… run id not yet visible",
    );

    expect(await screen.findByText("Run started")).toBeInTheDocument();
    expect(
      calls.filter((c) => c.startsWith("GET /api/launches/")),
    ).toHaveLength(2);
    expect(onRecord).toHaveBeenLastCalledWith(
      expect.objectContaining({ status: "started" }),
    );
    expect(props.onOpenRun).not.toHaveBeenCalled();
    expect(props.onOpenRunList).not.toHaveBeenCalled();
  });

  it("flips to the failure view if the process dies while waiting", async () => {
    stubApi({ get: { "/api/launches/": () => json(FAILED) } });
    render(
      <LaunchResultPanel
        {...panelProps({ initial: UNCONFIRMED, pollMs: 10 })}
      />,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Failed to start",
    );
  });

  it("stops after the bounded poll budget: Not confirmed yet, with explicit buttons only", async () => {
    const calls = stubApi({
      get: { "/api/launches/": () => json(UNCONFIRMED) },
    });
    const props = panelProps({ initial: UNCONFIRMED, pollMs: 5, maxPolls: 3 });
    render(<LaunchResultPanel {...props} />);

    expect(
      await screen.findByText(/Not confirmed yet — process still running/),
    ).toBeInTheDocument();
    expect(calls).toHaveLength(3);
    await new Promise((r) => setTimeout(r, 40));
    expect(calls).toHaveLength(3); // polling really stopped
    expect(props.onOpenRun).not.toHaveBeenCalled();
    expect(props.onOpenRunList).not.toHaveBeenCalled();

    await userEvent.click(
      screen.getByRole("button", { name: "Open run list" }),
    );
    expect(props.onOpenRunList).toHaveBeenCalledTimes(1);

    // Keep waiting restarts the bounded poll.
    await userEvent.click(screen.getByRole("button", { name: "Keep waiting" }));
    await waitFor(() => expect(calls.length).toBeGreaterThan(3));
    expect(await screen.findByText(/Not confirmed yet/)).toBeInTheDocument();
  });

  it("survives poll errors (keeps last state, still bounded)", async () => {
    const calls = stubApi({
      get: { "/api/launches/": () => json({ detail: "x" }, 500) },
    });
    render(
      <LaunchResultPanel
        {...panelProps({ initial: STARTING, pollMs: 5, maxPolls: 2 })}
      />,
    );
    expect(await screen.findByText(/Not confirmed yet/)).toBeInTheDocument();
    expect(calls).toHaveLength(2);
  });

  it("does not poll while its workspace tab is inactive", async () => {
    const calls = stubApi({ get: { "/api/launches/": () => json(STARTED) } });
    const { TabActiveContext } = await import("../tabs/context");
    render(
      <TabActiveContext.Provider value={false}>
        <LaunchResultPanel {...panelProps({ initial: STARTING, pollMs: 5 })} />
      </TabActiveContext.Provider>,
    );
    await act(async () => {
      await new Promise((r) => setTimeout(r, 40));
    });
    expect(calls).toHaveLength(0);
  });
});

const WORKFLOW_ROUTES = { "/api/workflows": () => json(WORKFLOWS) };

describe("NewRun launch flow (classic)", () => {
  async function submit(
    post: () => Response,
    onLaunched = vi.fn(),
    extra = {},
  ) {
    const calls = stubApi({ post, get: { ...WORKFLOW_ROUTES, ...extra } });
    render(<NewRun onLaunched={onLaunched} />);
    const prompt = await screen.findByLabelText("Prompt");
    await waitFor(() => expect(prompt).toBeEnabled());
    await userEvent.type(prompt, "keep me");
    await userEvent.click(screen.getByRole("button", { name: /start run/i }));
    return { calls, onLaunched, prompt };
  }

  it("failure: shows the alert, never navigates, Edit & retry keeps the form values", async () => {
    const { onLaunched, prompt } = await submit(() => json(FAILED, 201));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Failed to start");
    expect(alert).toHaveTextContent("Unknown repo_set: ai-models");
    expect(onLaunched).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: /Edit & retry/ }));
    expect(screen.queryByText("Failed to start")).not.toBeInTheDocument();
    expect(prompt).toHaveValue("keep me");
    expect(onLaunched).not.toHaveBeenCalled();
  });

  it("failure: Open run list navigates to the list only on click", async () => {
    const { onLaunched } = await submit(() => json(FAILED, 201));
    await screen.findByRole("alert");
    expect(onLaunched).not.toHaveBeenCalled();
    await userEvent.click(
      screen.getByRole("button", { name: "Open run list" }),
    );
    expect(onLaunched).toHaveBeenCalledExactlyOnceWith(null);
  });

  it("unconfirmed: waits without navigating", async () => {
    const { onLaunched } = await submit(() => json(UNCONFIRMED, 201), vi.fn(), {
      "/api/launches/": () => json(UNCONFIRMED),
    });
    expect(
      await screen.findByText(/Starting… run id not yet visible/),
    ).toBeInTheDocument();
    expect(onLaunched).not.toHaveBeenCalled();
  });

  it("started: Start another clears the prompt and the panel", async () => {
    const { onLaunched, prompt } = await submit(() => json(STARTED, 201));
    await screen.findByText("Run started");
    expect(onLaunched).not.toHaveBeenCalled();
    await userEvent.click(
      screen.getByRole("button", { name: "Start another" }),
    );
    expect(screen.queryByText("Run started")).not.toBeInTheDocument();
    expect(prompt).toHaveValue("");
  });

  it("a request rejected before spawning stays an inline form error (no panel)", async () => {
    const { onLaunched } = await submit(() =>
      json({ detail: "Unknown repo_set ai-models; available: fin-plan" }, 400),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "available: fin-plan",
    );
    expect(screen.queryByText("Failed to start")).not.toBeInTheDocument();
    expect(onLaunched).not.toHaveBeenCalled();
  });

  it("shows Starting… while the POST is in flight", async () => {
    let release: (r: Response) => void = () => {};
    const pending = new Promise<Response>((r) => (release = r));
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) =>
        init?.method === "POST"
          ? pending
          : url.startsWith("/api/workflows")
            ? json(WORKFLOWS)
            : json({}, 404),
      ),
    );
    render(<NewRun onLaunched={vi.fn()} />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /start run/i })).toBeEnabled(),
    );
    await userEvent.click(screen.getByRole("button", { name: /start run/i }));
    expect(
      await screen.findByText("Launching the engine process.", {
        exact: false,
      }),
    ).toBeInTheDocument();
    await act(async () => release(json(FAILED, 201)));
    expect(await screen.findByText("Failed to start")).toBeInTheDocument();
  });

  it("a failure is rehydrated after remount until dismissed; started is not remembered", async () => {
    await submit(() => json(FAILED, 201));
    await screen.findByRole("alert");
    expect(readStoredLaunchId("workflow")).toBe(FAILED.launch_id);
    document.body.innerHTML = "";

    stubApi({
      get: { ...WORKFLOW_ROUTES, "/api/launches/": () => json(FAILED) },
    });
    const second = render(<NewRun onLaunched={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Failed to start",
    );
    await userEvent.click(screen.getByRole("button", { name: /Edit & retry/ }));
    expect(readStoredLaunchId("workflow")).toBeNull();
    second.unmount();

    // A remembered launch that has since become `started` is silently dropped.
    writeStoredLaunchId("workflow", STARTED.launch_id);
    stubApi({
      get: { ...WORKFLOW_ROUTES, "/api/launches/": () => json(STARTED) },
    });
    render(<NewRun onLaunched={vi.fn()} />);
    await waitFor(() => expect(readStoredLaunchId("workflow")).toBeNull());
    expect(screen.queryByText("Run started")).not.toBeInTheDocument();
  });

  it("a stale remembered id (404) is forgotten", async () => {
    writeStoredLaunchId("workflow", "launch-gone");
    stubApi({ get: WORKFLOW_ROUTES });
    render(<NewRun onLaunched={vi.fn()} />);
    await waitFor(() => expect(readStoredLaunchId("workflow")).toBeNull());
  });
});

describe("TemplateLaunch 'Create & run' flow", () => {
  async function run(post: () => Response, onLaunched = vi.fn()) {
    stubApi({
      post,
      get: {
        "/api/templates": () => json(TEMPLATES),
        "/api/launches/": () => json(UNCONFIRMED),
      },
    });
    render(<TemplateLaunch onLaunched={onLaunched} />);
    await userEvent.selectOptions(
      await screen.findByLabelText(/repo_set/),
      "fin-plan",
    );
    await userEvent.click(screen.getByRole("button", { name: "Create & run" }));
    return onLaunched;
  }
  const response = (launch: LaunchRecord | null) =>
    json(
      {
        instance_dir: "/ws/runs/e-1",
        workflow_path: "/ws/runs/e-1/workflow.json",
        workflow: WORKFLOWS[0],
        launch,
      },
      201,
    );

  it("failure: alert + instance hint, no navigation until a button is clicked", async () => {
    const onLaunched = await run(() => response(FAILED));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Failed to start");
    expect(alert).toHaveTextContent("/ws/runs/e-1");
    expect(alert).toHaveTextContent("From workflow");
    expect(onLaunched).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: /Edit & retry/ }));
    expect(screen.getByLabelText(/repo_set/)).toHaveValue("fin-plan"); // form values kept
    expect(onLaunched).not.toHaveBeenCalled();
  });

  it("unconfirmed: waits, no navigation", async () => {
    const onLaunched = await run(() => response(UNCONFIRMED));
    expect(
      await screen.findByText(/Starting… run id not yet visible/),
    ).toBeInTheDocument();
    expect(onLaunched).not.toHaveBeenCalled();
  });

  it("started: Open run navigates on click only; Start another resets slug/prompt", async () => {
    const onLaunched = await run(() => response(STARTED));
    await screen.findByText("Run started");
    expect(onLaunched).not.toHaveBeenCalled();
    await userEvent.click(
      screen.getByRole("button", { name: "Start another" }),
    );
    expect(screen.queryByText("Run started")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Prompt")).toHaveValue("skeleton");
    expect(onLaunched).not.toHaveBeenCalled();
  });

  it("Open run list passes null", async () => {
    const onLaunched = await run(() => response(FAILED));
    await screen.findByRole("alert");
    await userEvent.click(
      screen.getByRole("button", { name: "Open run list" }),
    );
    expect(onLaunched).toHaveBeenCalledExactlyOnceWith(null);
  });

  it("a start response without launch info falls back to the created banner", async () => {
    const onLaunched = await run(() => response(null));
    expect(await screen.findByText(/Created at/)).toBeInTheDocument();
    expect(onLaunched).not.toHaveBeenCalled();
  });
});

describe("FailedLaunches strip", () => {
  it("renders nothing when empty", () => {
    const { container } = render(
      <FailedLaunches launches={[]} onDismiss={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("lists failures, lazy-loads the log on expand, and dismisses persistently", async () => {
    const calls = stubApi({ get: { "/api/launches/": () => json(FAILED) } });
    const onDismiss = vi.fn();
    render(
      <FailedLaunches
        launches={[{ ...FAILED, log_tail: undefined }]}
        onDismiss={onDismiss}
      />,
    );
    expect(
      screen.getByRole("region", { name: "Launches that failed to start" }),
    ).toHaveTextContent("1 launch failed to start");
    expect(calls).toHaveLength(0);
    await userEvent.click(screen.getByText(/\/ws\/wf.json/));
    expect(
      await screen.findByLabelText("Launch log (last lines)"),
    ).toHaveTextContent("Unknown repo_set");
    expect(calls).toHaveLength(1);

    await userEvent.click(
      screen.getByRole("button", { name: /Dismiss failed launch/ }),
    );
    expect(onDismiss).toHaveBeenCalledWith([FAILED.launch_id]);
    expect(readDismissedLaunches()).toEqual([FAILED.launch_id]);
  });

  it("shows a log-load error and pluralizes", async () => {
    stubApi({});
    render(
      <FailedLaunches
        launches={[
          { ...FAILED, workflow_path: null },
          { ...FAILED, launch_id: "launch-2", exit_code: null },
        ]}
        onDismiss={vi.fn()}
      />,
    );
    expect(screen.getByText("2 launches failed to start")).toBeInTheDocument();
    await userEvent.click(screen.getByText(/workflow from config/));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("loads the log once across collapse/expand, and reports a network failure", async () => {
    const calls = stubApi({ get: { "/api/launches/": () => json(FAILED) } });
    const first = render(
      <FailedLaunches launches={[FAILED]} onDismiss={vi.fn()} />,
    );
    const summary = screen.getByText(/\/ws\/wf.json/);
    await userEvent.click(summary); // open
    await screen.findByLabelText("Launch log (last lines)");
    await userEvent.click(summary); // close
    await userEvent.click(summary); // reopen
    expect(calls).toHaveLength(1);
    first.unmount();

    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("network down")),
    );
    render(<FailedLaunches launches={[FAILED]} onDismiss={vi.fn()} />);
    await userEvent.click(screen.getByText(/\/ws\/wf.json/));
    expect(await screen.findByRole("alert")).toHaveTextContent("network down");
  });

  it("an empty log reads as printed-nothing", async () => {
    stubApi({
      get: { "/api/launches/": () => json({ ...FAILED, log_tail: "" }) },
    });
    render(<FailedLaunches launches={[FAILED]} onDismiss={vi.fn()} />);
    await userEvent.click(screen.getByText(/\/ws\/wf.json/));
    expect(
      await screen.findByLabelText("Launch log (last lines)"),
    ).toHaveTextContent("(the process printed nothing)");
  });
});

describe("RunsList failed-launch surfacing", () => {
  const STATS = {
    total_runs: 0,
    runs_by_status: {},
    total_cost_usd: 0,
    total_tasks: 0,
    tasks_by_status: {},
    total_input_tokens: 0,
    total_output_tokens: 0,
    total_wall_seconds: 0,
    total_active_seconds: 0,
  };

  it("shows recent failed-to-start launches (24h filter) and hides dismissed ones", async () => {
    const calls = stubApi({
      get: {
        "/api/runs/stats": () => json(STATS),
        "/api/runs": () => json([]),
        "/api/launches": () =>
          json([FAILED, { ...FAILED, launch_id: "launch-other" }]),
      },
    });
    localStorage.setItem(
      "ao.launch.dismissed",
      JSON.stringify(["launch-other"]),
    );
    render(<RunsList onOpen={vi.fn()} />);
    expect(
      await screen.findByText("1 launch failed to start"),
    ).toBeInTheDocument();
    expect(
      calls.some((c) =>
        c.includes("/api/launches?status=failed_to_start&since_hours=24"),
      ),
    ).toBe(true);

    await userEvent.click(
      screen.getByRole("button", { name: /Dismiss failed launch/ }),
    );
    await waitFor(() =>
      expect(screen.queryByText(/failed to start/)).not.toBeInTheDocument(),
    );
  });

  it("an old backend (launches 404) or a malformed reply leaves the run list working", async () => {
    stubApi({
      get: {
        "/api/runs/stats": () => json(STATS),
        "/api/runs": () => json([]),
      },
    });
    render(<RunsList onOpen={vi.fn()} />);
    expect(await screen.findByText(/No runs yet/)).toBeInTheDocument();
    expect(screen.queryByRole("region")).not.toBeInTheDocument();
  });
});
