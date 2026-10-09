import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  DEFAULT_GRAPH_PREFS,
  edgesForView,
  joinNodes,
  metricFraction,
  nodesForView,
  PREFS_STORAGE_KEY,
  readPrefs,
  relatedIds,
  searchNodes,
  SEARCH_MAX_RESULTS,
  waitSeconds,
  writePrefs,
  type GraphPrefs,
} from "../graph/model";
import type { DependencyEdge, GraphNode, RunGraph, SpawnEdge, TaskStat } from "../types";
import fixtureData from "./fixtures/run-graph.json";

const fixture = fixtureData as unknown as RunGraph;

// ---- Minimal builders so each test only spells out the fields it cares about ----------------

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

function makeStat(overrides: Partial<TaskStat> & { id: string }): TaskStat {
  return {
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

// ---- joinNodes --------------------------------------------------------------------------------

describe("joinNodes", () => {
  it("attaches the matching TaskStat by id", () => {
    const graph = makeGraph({ nodes: [makeNode({ id: "cp1" })] });
    const stat = makeStat({ id: "cp1", status: "running" });
    const [viewNode] = joinNodes(graph, [stat]);
    expect(viewNode.stat).toBe(stat);
    expect(viewNode.id).toBe("cp1");
  });

  it("attaches null when the task hasn't reached RunDetail.tasks yet", () => {
    const graph = makeGraph({ nodes: [makeNode({ id: "cp1" })] });
    const [viewNode] = joinNodes(graph, []);
    expect(viewNode.stat).toBeNull();
  });

  it("joins every node in the shared contract fixture without throwing", () => {
    const stats = fixture.nodes.map((n) => makeStat({ id: n.id }));
    const joined = joinNodes(fixture, stats);
    expect(joined).toHaveLength(fixture.nodes.length);
    expect(joined.every((n) => n.stat !== null)).toBe(true);
  });
});

// ---- edgesForView -------------------------------------------------------------------------------

describe("edgesForView", () => {
  it("returns exactly dependency_edges, id-prefixed dep:, for the dependency view", () => {
    const edges = edgesForView(fixture, "dependency");
    expect(edges).toHaveLength(fixture.dependency_edges.length);
    for (const [i, e] of edges.entries()) {
      expect(e.id).toBe(`dep:${fixture.dependency_edges[i].source}->${fixture.dependency_edges[i].target}`);
      expect(e.set).toBe("dependency");
    }
  });

  it("returns exactly spawn_edges, id-prefixed spawn:, for the spawn view", () => {
    const edges = edgesForView(fixture, "spawn");
    expect(edges).toHaveLength(fixture.spawn_edges.length);
    for (const [i, e] of edges.entries()) {
      expect(e.id).toBe(`spawn:${fixture.spawn_edges[i].source}->${fixture.spawn_edges[i].target}`);
      expect(e.set).toBe("spawn");
    }
  });
});

// ---- nodesForView -------------------------------------------------------------------------------

describe("nodesForView", () => {
  const nodes = [
    { ...makeNode({ id: "a" }), stat: null },
    { ...makeNode({ id: "b" }), stat: null },
    { ...makeNode({ id: "c" }), stat: null },
  ];

  it("the dependency view always shows every node", () => {
    const edges = edgesForView(makeGraph(), "dependency");
    const { visible, hiddenCount } = nodesForView(nodes, edges, "dependency", false);
    expect(visible).toHaveLength(3);
    expect(hiddenCount).toBe(0);
  });

  it("the spawn view with showUnrelated shows every node", () => {
    const { visible, hiddenCount } = nodesForView(nodes, [], "spawn", true);
    expect(visible).toHaveLength(3);
    expect(hiddenCount).toBe(0);
  });

  it("the spawn view without showUnrelated hides nodes with no spawn edge", () => {
    const spawnEdges: SpawnEdge[] = [{ source: "a", target: "b", origin: "injected", loop_id: null, iteration: null }];
    const graph = makeGraph({ spawn_edges: spawnEdges });
    const edges = edgesForView(graph, "spawn");
    const { visible, hiddenCount } = nodesForView(nodes, edges, "spawn", false);
    expect(visible.map((n) => n.id).sort()).toEqual(["a", "b"]);
    expect(hiddenCount).toBe(1);
  });
});

// ---- metricFraction -----------------------------------------------------------------------------

describe("metricFraction", () => {
  const maxima = { duration: 100, cost: 10 };

  it("returns null for mode 'none'", () => {
    expect(metricFraction(makeStat({ id: "a", duration_seconds: 50 }), "none", maxima)).toBeNull();
  });

  it("returns null for a null stat", () => {
    expect(metricFraction(null, "duration", maxima)).toBeNull();
  });

  it("returns null when the stat's own value is null (still running / never recorded)", () => {
    expect(metricFraction(makeStat({ id: "a", duration_seconds: null }), "duration", maxima)).toBeNull();
  });

  it("returns null when the maximum is <= 0", () => {
    expect(metricFraction(makeStat({ id: "a", duration_seconds: 50 }), "duration", { duration: 0, cost: 10 })).toBeNull();
    expect(
      metricFraction(makeStat({ id: "a", duration_seconds: 50 }), "duration", { duration: -1, cost: 10 }),
    ).toBeNull();
  });

  it("clamps a value within range to [0, 1]", () => {
    expect(metricFraction(makeStat({ id: "a", duration_seconds: 25 }), "duration", maxima)).toBe(0.25);
    expect(metricFraction(makeStat({ id: "a", cost_usd: 5 }), "cost", maxima)).toBe(0.5);
  });

  it("clamps a value above the maximum to 1", () => {
    expect(metricFraction(makeStat({ id: "a", duration_seconds: 500 }), "duration", maxima)).toBe(1);
  });
});

// ---- searchNodes --------------------------------------------------------------------------------

describe("searchNodes", () => {
  const nodes = [
    makeNode({ id: "cp1", label: "checkpoint one" }),
    makeNode({ id: "u1", label: "Unit Task" }),
    makeNode({ id: "u2", label: "another" }),
  ];

  it("returns [] for an empty query", () => {
    expect(searchNodes(nodes, "")).toEqual([]);
  });

  it("matches case-insensitively over id", () => {
    expect(searchNodes(nodes, "CP1")).toEqual(["cp1"]);
  });

  it("matches case-insensitively over label, substring", () => {
    expect(searchNodes(nodes, "unit")).toEqual(["u1"]);
  });

  it("preserves input order across multiple matches", () => {
    expect(searchNodes(nodes, "u")).toEqual(["u1", "u2"]);
  });

  it(`caps results at SEARCH_MAX_RESULTS (${SEARCH_MAX_RESULTS})`, () => {
    const many = Array.from({ length: SEARCH_MAX_RESULTS + 10 }, (_, i) => makeNode({ id: `match-${i}` }));
    const results = searchNodes(many, "match");
    expect(results).toHaveLength(SEARCH_MAX_RESULTS);
    expect(results).toEqual(many.slice(0, SEARCH_MAX_RESULTS).map((n) => n.id));
  });
});

// ---- relatedIds ---------------------------------------------------------------------------------

describe("relatedIds", () => {
  it("derives parent/children from spawn_edges and dependsOn/dependents from dependency_edges", () => {
    const related = relatedIds(fixture, "u1");
    expect(related.parent).toBe("cp1");
    expect(related.children).toEqual(["leaf​"]);
    expect(related.dependsOn).toEqual(["cp1"]);
    expect(related.dependents).toEqual(["leaf​"]);
  });

  it("sorts every array field", () => {
    const dependencyEdges: DependencyEdge[] = [
      { source: "z", target: "x", kind: "explicit", via: null },
      { source: "a", target: "x", kind: "explicit", via: null },
    ];
    const graph = makeGraph({ dependency_edges: dependencyEdges });
    expect(relatedIds(graph, "x").dependsOn).toEqual(["a", "z"]);
  });

  it("returns null parent and empty arrays for an unrelated id", () => {
    const graph = makeGraph();
    expect(relatedIds(graph, "lonely")).toEqual({
      parent: null,
      children: [],
      dependsOn: [],
      dependents: [],
    });
  });
});

// ---- waitSeconds --------------------------------------------------------------------------------

describe("waitSeconds", () => {
  it("returns 0 for a task with no dependencies", () => {
    const graph = makeGraph();
    const stats = new Map([["a", makeStat({ id: "a", started_at: "2026-01-01T00:00:10Z" })]]);
    expect(waitSeconds("a", graph, stats)).toBe(0);
  });

  it("computes started_at minus the max ended_at across dependency sources", () => {
    const graph = makeGraph({
      dependency_edges: [
        { source: "p1", target: "c", kind: "explicit", via: null },
        { source: "p2", target: "c", kind: "explicit", via: null },
      ],
    });
    const stats = new Map([
      ["p1", makeStat({ id: "p1", ended_at: "2026-01-01T00:00:05Z" })],
      ["p2", makeStat({ id: "p2", ended_at: "2026-01-01T00:00:08Z" })],
      ["c", makeStat({ id: "c", started_at: "2026-01-01T00:00:10Z" })],
    ]);
    expect(waitSeconds("c", graph, stats)).toBe(2);
  });

  it("returns null when the task itself has no started_at", () => {
    const graph = makeGraph();
    const stats = new Map([["a", makeStat({ id: "a", started_at: null })]]);
    expect(waitSeconds("a", graph, stats)).toBeNull();
  });

  it("returns null when the task has no stat at all", () => {
    expect(waitSeconds("missing", makeGraph(), new Map())).toBeNull();
  });

  it("returns null when a dependency source has no stat", () => {
    const graph = makeGraph({
      dependency_edges: [{ source: "p1", target: "c", kind: "explicit", via: null }],
    });
    const stats = new Map([["c", makeStat({ id: "c", started_at: "2026-01-01T00:00:10Z" })]]);
    expect(waitSeconds("c", graph, stats)).toBeNull();
  });

  it("returns null when a dependency source has no ended_at (still running)", () => {
    const graph = makeGraph({
      dependency_edges: [{ source: "p1", target: "c", kind: "explicit", via: null }],
    });
    const stats = new Map([
      ["p1", makeStat({ id: "p1", ended_at: null })],
      ["c", makeStat({ id: "c", started_at: "2026-01-01T00:00:10Z" })],
    ]);
    expect(waitSeconds("c", graph, stats)).toBeNull();
  });

  it("clamps a negative gap (retry started before a source's prior ended_at) to 0", () => {
    const graph = makeGraph({
      dependency_edges: [{ source: "p1", target: "c", kind: "explicit", via: null }],
    });
    const stats = new Map([
      ["p1", makeStat({ id: "p1", ended_at: "2026-01-01T00:01:00Z" })],
      ["c", makeStat({ id: "c", started_at: "2026-01-01T00:00:00Z" })],
    ]);
    expect(waitSeconds("c", graph, stats)).toBe(0);
  });
});

// ---- readPrefs / writePrefs -----------------------------------------------------------------------

describe("readPrefs / writePrefs", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("returns the documented defaults when nothing is stored", () => {
    expect(readPrefs()).toEqual(DEFAULT_GRAPH_PREFS);
  });

  it("round-trips a valid preference object", () => {
    const prefs: GraphPrefs = { tab: "graph", view: "spawn", metric: "cost", showUnrelated: true, hideRedundantEdges: false };
    writePrefs(prefs);
    expect(readPrefs()).toEqual(prefs);
    expect(localStorage.getItem(PREFS_STORAGE_KEY)).toBe(JSON.stringify(prefs));
  });

  it("falls back to defaults on invalid JSON", () => {
    localStorage.setItem(PREFS_STORAGE_KEY, "{not json");
    expect(readPrefs()).toEqual(DEFAULT_GRAPH_PREFS);
  });

  it("falls back field-by-field when individual values are garbage", () => {
    localStorage.setItem(
      PREFS_STORAGE_KEY,
      JSON.stringify({ tab: "bogus", view: "spawn", metric: 42, showUnrelated: "yes" }),
    );
    expect(readPrefs()).toEqual({ ...DEFAULT_GRAPH_PREFS, view: "spawn" });
  });

  it("falls back to defaults when the stored value isn't an object", () => {
    localStorage.setItem(PREFS_STORAGE_KEY, JSON.stringify("just a string"));
    expect(readPrefs()).toEqual(DEFAULT_GRAPH_PREFS);
  });

  it("falls back to defaults when localStorage.getItem throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError: storage disabled");
    });
    expect(readPrefs()).toEqual(DEFAULT_GRAPH_PREFS);
  });

  it("writePrefs never throws even when localStorage.setItem throws", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });
    expect(() => writePrefs(DEFAULT_GRAPH_PREFS)).not.toThrow();
  });
});
