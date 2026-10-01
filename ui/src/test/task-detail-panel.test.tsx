import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TaskDetailPanel } from "../graph/TaskDetailPanel";
import { relatedIds, waitSeconds } from "../graph/model";
import { formatCost, formatDuration } from "../format";
import type { GraphNode, RunGraph, SpawnEdge, TaskStat } from "../types";
import fixtureData from "./fixtures/run-graph.json";

/**
 * `TaskDetailPanel` presentational/interaction tests (`T-pAi0Cv` AC-2, AC-3 (callback wiring
 * only -- focus-RETURN-to-node needs the real canvas DOM, covered at the RunGraph integration
 * level in `run-graph-detail-reveal.test.tsx`), AC-5, AC-6, AC-7, AC-8).
 *
 * No `@xyflow/react` involved at all -- this component is a plain `<aside>`, so it's tested
 * standalone, the same level `graph-toolbar.test.tsx`/`graph-legend.test.tsx` (`T-aHktGB`) test
 * their own presentational components at.
 */

const fixture = fixtureData as unknown as RunGraph;

const BASE_STAT: TaskStat = {
  id: "n",
  status: "succeeded",
  attempts: 1,
  started_at: null,
  ended_at: null,
  duration_seconds: null,
  input_tokens: 0,
  output_tokens: 0,
  cost_usd: 0,
  origin: "static",
  route: null,
  output_artifact_path: null,
  outputs: [],
  integration_status: null,
  tier_reached: null,
  conflicted_count: 0,
  cache_read_tokens: 0,
  cache_creation_tokens: 0,
  cache_hit_rate: null,
  dispatch_cycle: 1,
  not_taken_reason: null,
};

function makeNode(overrides: Partial<GraphNode> & { id: string }): GraphNode {
  return {
    label: overrides.id,
    label_sanitized: false,
    origin: "static",
    parent_task_id: null,
    route: null,
    loop_id: null,
    iteration: null,
    spawn_depth: 0,
    children_count: 0,
    is_emitter: false,
    is_router: false,
    is_loop_gate: false,
    exec_ordinal: null,
    missing: false,
    ...overrides,
  };
}

function makeGraph(overrides: Partial<RunGraph> = {}): RunGraph {
  return {
    schema_version: 1,
    run_id: "r1",
    graph_version: "v1",
    source: "snapshot",
    spawn_data: "recorded",
    truncated: false,
    warnings: [],
    nodes: [],
    dependency_edges: [],
    spawn_edges: [],
    loops: [],
    routers: [],
    ...overrides,
  };
}

/** Restores a real (jsdom default) viewport width after a test overrides it. */
const ORIGINAL_INNER_WIDTH = window.innerWidth;

afterEach(() => {
  Object.defineProperty(window, "innerWidth", { value: ORIGINAL_INNER_WIDTH, configurable: true });
  vi.restoreAllMocks();
});

describe("TaskDetailPanel (AC-2): shared fixture, real relationships", () => {
  it("renders u1's header, timing, usage, retries, spawn parent, and dependency links", () => {
    const onClose = vi.fn();
    const onNavigate = vi.fn();
    const u1Stat: TaskStat = {
      ...BASE_STAT,
      id: "u1",
      attempts: 2,
      started_at: "2026-09-21T10:01:00+00:00",
      ended_at: "2026-09-21T10:05:00+00:00",
      duration_seconds: 240,
      cost_usd: 1.5,
      input_tokens: 500,
      output_tokens: 100,
      dispatch_cycle: 2,
    };
    const cp1Stat: TaskStat = { ...BASE_STAT, id: "cp1", ended_at: "2026-09-21T10:00:30+00:00" };
    const statsById = new Map([
      ["u1", u1Stat],
      ["cp1", cp1Stat],
    ]);

    render(
      <TaskDetailPanel
        nodeId="u1"
        graph={fixture}
        statsById={statsById}
        onClose={onClose}
        onNavigate={onNavigate}
      />,
    );

    const panel = screen.getByRole("complementary", { name: "Task details" });
    expect(within(panel).getByRole("heading", { level: 2 }).textContent).toBe("u1");

    // Timing: exact formatDuration + the model's own waitSeconds, formatted the same way.
    const expectedWait = formatDuration(waitSeconds("u1", fixture, statsById));
    expect(panel.textContent).toContain(formatDuration(240));
    expect(panel.textContent).toContain(expectedWait);

    // Usage
    expect(panel.textContent).toContain(formatCost(1.5));

    // Retries: attempts 2, retries 1, dispatches 2 (dispatch_cycle)
    const retriesSection = within(panel).getByRole("region", { name: "Retries" });
    expect(retriesSection.textContent).toContain("2"); // attempts
    expect(retriesSection.textContent).toContain("1"); // retries = attempts - 1

    // Spawn: "parent: cp1" (real fixture relationship: u1's spawn parent is cp1)
    expect(panel.textContent).toMatch(/parent:\s*cp1/);

    // Dependencies: cp1 under "Depends on" (real fixture edge cp1 -> u1, explicit); the real
    // dependent is the sanitized "leaf" node (there is no "cp2" in the shared fixture).
    const rel = relatedIds(fixture, "u1");
    const dependenciesSection = within(panel).getByRole("region", { name: "Dependencies" });
    expect(within(dependenciesSection).getByText("cp1")).toBeInTheDocument();
    expect(rel.dependents).toHaveLength(1);
    expect(within(dependenciesSection).getByText("leaf")).toBeInTheDocument();
  });
});

describe("TaskDetailPanel (AC-3): close callbacks", () => {
  it("Esc calls onClose", async () => {
    const onClose = vi.fn();
    render(
      <TaskDetailPanel
        nodeId="u1"
        graph={makeGraph({ nodes: [makeNode({ id: "u1" })] })}
        statsById={new Map()}
        onClose={onClose}
        onNavigate={vi.fn()}
      />,
    );
    const panel = screen.getByRole("complementary", { name: "Task details" });
    panel.focus();
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("the × button calls onClose", async () => {
    const onClose = vi.fn();
    render(
      <TaskDetailPanel
        nodeId="u1"
        graph={makeGraph({ nodes: [makeNode({ id: "u1" })] })}
        statsById={new Map()}
        onClose={onClose}
        onNavigate={vi.fn()}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("renders nothing when nodeId is null", () => {
    const { container } = render(
      <TaskDetailPanel
        nodeId={null}
        graph={makeGraph()}
        statsById={new Map()}
        onClose={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );
    expect(container.firstChild).toBeNull();
  });
});

describe("TaskDetailPanel (AC-4): every link calls onNavigate with the target id", () => {
  it("clicking the cp1 parent link calls onNavigate('cp1')", async () => {
    const onNavigate = vi.fn();
    render(
      <TaskDetailPanel
        nodeId="u1"
        graph={fixture}
        statsById={new Map()}
        onClose={vi.fn()}
        onNavigate={onNavigate}
      />,
    );
    const spawnSection = screen.getByRole("region", { name: "Spawn" });
    await userEvent.click(within(spawnSection).getByRole("button", { name: "cp1" }));
    expect(onNavigate).toHaveBeenCalledWith("cp1");
  });
});

describe("TaskDetailPanel (AC-5): children cap", () => {
  function makeEmitterGraph(childCount: number): RunGraph {
    const childIds = Array.from({ length: childCount }, (_, i) => `child-${String(i).padStart(2, "0")}`);
    return makeGraph({
      nodes: [
        makeNode({ id: "emitter", is_emitter: true, children_count: childCount }),
        ...childIds.map((id) => makeNode({ id, parent_task_id: "emitter" })),
      ],
      spawn_edges: childIds.map(
        (id): SpawnEdge => ({
          source: "emitter",
          target: id,
          origin: "injected",
          loop_id: null,
          iteration: null,
        }),
      ),
    });
  }

  it("shows 20 children and a 'show all (45)' toggle; clicking it reveals all 45", async () => {
    const graph = makeEmitterGraph(45);
    render(
      <TaskDetailPanel
        nodeId="emitter"
        graph={graph}
        statsById={new Map()}
        onClose={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );
    const spawnSection = screen.getByRole("region", { name: "Spawn" });
    expect(within(spawnSection).getAllByRole("listitem")).toHaveLength(20);
    const showAll = within(spawnSection).getByRole("button", { name: "show all (45)" });

    await userEvent.click(showAll);

    expect(within(spawnSection).getAllByRole("listitem")).toHaveLength(45);
    expect(within(spawnSection).queryByRole("button", { name: /show all/ })).toBeNull();
  });
});

describe("TaskDetailPanel (AC-6): degraded data", () => {
  it("stat === null shows 'Stats pending' with no crash", () => {
    expect(() =>
      render(
        <TaskDetailPanel
          nodeId="u1"
          graph={makeGraph({ nodes: [makeNode({ id: "u1" })] })}
          statsById={new Map()}
          onClose={vi.fn()}
          onNavigate={vi.fn()}
        />,
      ),
    ).not.toThrow();
    expect(screen.getAllByText("Stats pending").length).toBeGreaterThan(0);
  });

  it("started_at null renders wait as em dash while other stats still show", () => {
    const statsById = new Map([
      ["u1", { ...BASE_STAT, id: "u1", started_at: null, duration_seconds: 12 }],
    ]);
    render(
      <TaskDetailPanel
        nodeId="u1"
        graph={makeGraph({ nodes: [makeNode({ id: "u1" })] })}
        statsById={statsById}
        onClose={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );
    expect(screen.queryByText("Stats pending")).toBeNull();
    const timingSection = screen.getByRole("region", { name: "Timing" });
    expect(within(timingSection).getByText("Wait before start").nextSibling?.textContent).toBe("—");
  });

  it("missing=true shows the unknown-task note without crashing", () => {
    render(
      <TaskDetailPanel
        nodeId="missing-dep"
        graph={fixture}
        statsById={new Map()}
        onClose={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );
    expect(
      screen.getByText("Unknown task (referenced by a dependency but never defined)"),
    ).toBeInTheDocument();
  });
});

describe("TaskDetailPanel (AC-7/D-5): text-only rendering", () => {
  const malicious = "<b>x</b>";
  // "evil" depends on "dep-of-evil" -- so "dep-of-evil"'s own panel lists "evil" (the malicious
  // label) as a DEPENDENT link, exercising the label-rendering path a related-task link uses.
  const graph = makeGraph({
    nodes: [makeNode({ id: "evil", label: malicious }), makeNode({ id: "dep-of-evil" })],
    dependency_edges: [{ source: "dep-of-evil", target: "evil", kind: "explicit", via: null }],
  });

  it("renders literally in the header", () => {
    const { container } = render(
      <TaskDetailPanel
        nodeId="evil"
        graph={graph}
        statsById={new Map()}
        onClose={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );
    expect(container.querySelector("b")).toBeNull();
    expect(screen.getByRole("heading", { level: 2 }).textContent).toBe(malicious);
  });

  it("renders literally in a related-task link", () => {
    const { container } = render(
      <TaskDetailPanel
        nodeId="dep-of-evil"
        graph={graph}
        statsById={new Map()}
        onClose={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );
    expect(container.querySelector("b")).toBeNull();
    expect(screen.getByRole("button", { name: malicious })).toBeInTheDocument();
  });
});

describe("TaskDetailPanel (AC-8): responsive class", () => {
  it("uses the bottom-sheet class at a 600px viewport width", () => {
    Object.defineProperty(window, "innerWidth", { value: 600, configurable: true });
    render(
      <TaskDetailPanel
        nodeId="u1"
        graph={makeGraph({ nodes: [makeNode({ id: "u1" })] })}
        statsById={new Map()}
        onClose={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );
    const panel = screen.getByRole("complementary", { name: "Task details" });
    expect(panel.className).toContain("task-detail-panel--sheet");
    expect(panel.className).not.toContain("task-detail-panel--side");
  });

  it("uses the side class at a 1200px viewport width", () => {
    Object.defineProperty(window, "innerWidth", { value: 1200, configurable: true });
    render(
      <TaskDetailPanel
        nodeId="u1"
        graph={makeGraph({ nodes: [makeNode({ id: "u1" })] })}
        statsById={new Map()}
        onClose={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );
    const panel = screen.getByRole("complementary", { name: "Task details" });
    expect(panel.className).toContain("task-detail-panel--side");
    expect(panel.className).not.toContain("task-detail-panel--sheet");
  });
});
