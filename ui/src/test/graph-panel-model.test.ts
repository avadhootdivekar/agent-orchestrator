import { describe, expect, it } from "vitest";
import { panelModel, relatedIds, waitSeconds } from "../graph/model";
import type { DependencyEdge, GraphNode, RunGraph, SpawnEdge, TaskStat } from "../types";
import fixtureData from "./fixtures/run-graph.json";

/**
 * `panelModel` pure-function tests (`T-pAi0Cv` AC-2, AC-5, AC-6). A NEW file rather than editing
 * `graph-model.test.ts` (T-adVpTj) -- keeps this task's change to that module fully additive.
 *
 * Uses the shared contract fixture for the real-relationship assertions (AC-2), and small local
 * `makeGraph`/`makeNode`/`makeStat` builders (same pattern `graph-model.test.ts` already uses)
 * for the synthetic-graph cases (AC-5's 45-child emitter) the fixture doesn't contain.
 */

const fixture = fixtureData as unknown as RunGraph;

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

describe("panelModel (AC-2): shared fixture, real relationships", () => {
  it("assembles u1's full panel data from the fixture's actual edges", () => {
    const u1Stat = makeStat({
      id: "u1",
      attempts: 2,
      started_at: "2026-09-21T10:01:00+00:00",
      ended_at: "2026-09-21T10:05:00+00:00",
      duration_seconds: 240,
      cost_usd: 1.5,
      input_tokens: 500,
      output_tokens: 100,
      dispatch_cycle: 2,
    });
    const cp1Stat = makeStat({ id: "cp1", ended_at: "2026-09-21T10:00:30+00:00" });
    const statsById = new Map([
      ["u1", u1Stat],
      ["cp1", cp1Stat],
    ]);

    const panel = panelModel("u1", fixture, statsById);

    expect(panel.hasStat).toBe(true);
    expect(panel.header).toEqual({
      label: "u1",
      sanitized: false,
      status: "succeeded",
      origin: "injected",
      route: null,
      missing: false,
    });
    expect(panel.timing.duration).toBe(240);
    expect(panel.timing.wait).toBe(waitSeconds("u1", fixture, statsById));
    expect(panel.usage.cost).toBe(1.5);
    expect(panel.retries).toEqual({ attempts: 2, retries: 1, dispatches: 2 });
    // Real fixture relationships (not invented): u1's spawn parent is cp1 (spawn_edges), and its
    // dependency neighbors come from dependency_edges -- cp1 upstream, the sanitized "leaf" node
    // downstream (there is no "cp2" node in the shared fixture).
    const rel = relatedIds(fixture, "u1");
    expect(panel.spawn.parent).toBe("cp1");
    expect(panel.deps.dependsOn).toEqual(rel.dependsOn);
    expect(panel.deps.dependsOn).toContain("cp1");
    expect(panel.deps.dependents).toEqual(rel.dependents);
    expect(panel.deps.dependents).toHaveLength(1);
  });

  it("returns hasStat=false and default usage/outcome when stat is null (AC-6)", () => {
    const panel = panelModel("u1", fixture, new Map());
    expect(panel.hasStat).toBe(false);
    expect(panel.header.status).toBe("pending");
    expect(panel.usage).toEqual({
      cost: 0,
      inputTokens: 0,
      outputTokens: 0,
      cacheHitRate: null,
      cacheReadTokens: 0,
      cacheCreationTokens: 0,
    });
    expect(panel.retries).toEqual({ attempts: null, retries: 0, dispatches: null });
    expect(panel.outcome.outputs).toEqual([]);
  });

  it("wait is null when started_at is null even though a stat exists (AC-6)", () => {
    const statsById = new Map([["u1", makeStat({ id: "u1", started_at: null })]]);
    const panel = panelModel("u1", fixture, statsById);
    expect(panel.hasStat).toBe(true);
    expect(panel.timing.wait).toBeNull();
  });

  it("a missing node's header.missing is true (AC-6)", () => {
    const panel = panelModel("missing-dep", fixture, new Map());
    expect(panel.header.missing).toBe(true);
    // Real fixture relationship: nothing depends on missing-dep's non-existent output, but
    // `gate` depends on it -- exercised so the panel's Dependencies section has real content too.
    expect(panel.deps.dependents).toEqual(["gate"]);
  });
});

describe("panelModel (AC-5): unbounded children -- the component applies the preview cap", () => {
  it("returns all 45 children, not sliced", () => {
    const childIds = Array.from({ length: 45 }, (_, i) => `child-${String(i).padStart(2, "0")}`);
    const graph = makeGraph({
      nodes: [
        makeNode({ id: "emitter", is_emitter: true, children_count: 45 }),
        ...childIds.map((id) => makeNode({ id, parent_task_id: "emitter" })),
      ],
      spawn_edges: childIds.map(
        (id): SpawnEdge => ({ source: "emitter", target: id, origin: "injected", loop_id: null, iteration: null }),
      ),
    });
    const panel = panelModel("emitter", graph, new Map());
    expect(panel.spawn.children).toHaveLength(45);
    expect(panel.spawn.children).toEqual([...childIds].sort());
  });
});

describe("panelModel: unknown id falls back to a well-formed model rather than throwing", () => {
  it("never crashes for an id absent from graph.nodes", () => {
    const graph = makeGraph({
      nodes: [makeNode({ id: "a" })],
      dependency_edges: [{ source: "ghost", target: "a", kind: "explicit", via: null } as DependencyEdge],
    });
    expect(() => panelModel("ghost", graph, new Map())).not.toThrow();
    const panel = panelModel("ghost", graph, new Map());
    expect(panel.header.label).toBe("ghost");
    expect(panel.header.missing).toBe(true);
    expect(panel.deps.dependents).toEqual(["a"]);
  });
});
