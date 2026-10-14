import { describe, expect, it } from "vitest";
import { computeLayout, redundantEdges, transitiveReduction } from "../graph/layout";
import { NODE_HEIGHT, NODE_SEP, NODE_WIDTH } from "../graph/model";

/**
 * AC-5's perf assertion (≤ 900 ms, 3x the NFR-3 300 ms target to absorb CI variance) needs a
 * generated 200-node/500-edge graph. Built deterministically (no `random()`, per CLAUDE.md's
 * determinism rule) as a chain plus increasing-offset skip edges, so the test itself never
 * flakes on graph shape.
 */
const PERF_NODE_COUNT = 200;
const PERF_EDGE_COUNT = 500;

function generateChainGraph(nodeCount: number, edgeCount: number) {
  const nodes = Array.from({ length: nodeCount }, (_, i) => ({ id: `t${i}` }));
  const edges: { source: string; target: string }[] = [];

  for (let i = 1; i < nodeCount; i++) {
    edges.push({ source: `t${i - 1}`, target: `t${i}` });
  }

  let offset = 2;
  while (edges.length < edgeCount && offset < nodeCount) {
    for (let i = 0; i + offset < nodeCount && edges.length < edgeCount; i++) {
      edges.push({ source: `t${i}`, target: `t${i + offset}` });
    }
    offset += 1;
  }

  return { nodes, edges: edges.slice(0, edgeCount) };
}

function boxesOverlap(a: { x: number; y: number }, b: { x: number; y: number }): boolean {
  return a.x < b.x + NODE_WIDTH && a.x + NODE_WIDTH > b.x && a.y < b.y + NODE_HEIGHT && a.y + NODE_HEIGHT > b.y;
}

describe("computeLayout", () => {
  it("uses LR for the dependency view", async () => {
    const result = await computeLayout([{ id: "a" }, { id: "b" }], [{ source: "a", target: "b" }], "dependency");
    expect(result.direction).toBe("LR");
  });

  it("uses TB for the spawn view", async () => {
    const result = await computeLayout([{ id: "a" }, { id: "b" }], [{ source: "a", target: "b" }], "spawn");
    expect(result.direction).toBe("TB");
  });

  it("positions every input node", async () => {
    const nodes = [{ id: "a" }, { id: "b" }, { id: "c" }];
    const result = await computeLayout(nodes, [{ source: "a", target: "b" }], "dependency");
    expect(result.positions.size).toBe(3);
    for (const n of nodes) {
      const pos = result.positions.get(n.id);
      expect(pos).toBeDefined();
      expect(Number.isFinite(pos!.x)).toBe(true);
      expect(Number.isFinite(pos!.y)).toBe(true);
    }
  });

  it("ignores edges whose endpoints are missing, without throwing", async () => {
    const nodes = [{ id: "a" }, { id: "b" }];
    const edges = [
      { source: "a", target: "nonexistent" },
      { source: "nonexistent", target: "b" },
      { source: "a", target: "b" },
    ];
    const result = await computeLayout(nodes, edges, "dependency");
    expect(Array.from(result.positions.keys()).sort()).toEqual(["a", "b"]);
  });

  it("tolerates a cycle without throwing", async () => {
    const nodes = [{ id: "a" }, { id: "b" }];
    const edges = [
      { source: "a", target: "b" },
      { source: "b", target: "a" },
    ];
    await expect(computeLayout(nodes, edges, "dependency")).resolves.toBeDefined();
    const result = await computeLayout(nodes, edges, "dependency");
    expect(result.positions.size).toBe(2);
  });

  it("is deterministic: identical input yields identical output", async () => {
    const { nodes, edges } = generateChainGraph(PERF_NODE_COUNT, PERF_EDGE_COUNT);
    const first = await computeLayout(nodes, edges, "dependency");
    const second = await computeLayout(nodes, edges, "dependency");
    expect(first.direction).toBe(second.direction);
    expect(Array.from(first.positions.entries())).toEqual(Array.from(second.positions.entries()));
  });

  it(`lays out ${PERF_NODE_COUNT} nodes with no two overlapping (bounding boxes of NODE_WIDTH x NODE_HEIGHT)`, async () => {
    const { nodes, edges } = generateChainGraph(PERF_NODE_COUNT, PERF_EDGE_COUNT);
    const result = await computeLayout(nodes, edges, "dependency");
    const positions = nodes.map((n) => result.positions.get(n.id)!);

    for (let i = 0; i < positions.length; i++) {
      for (let j = i + 1; j < positions.length; j++) {
        expect(boxesOverlap(positions[i], positions[j]), `nodes ${nodes[i].id} and ${nodes[j].id} overlap`).toBe(
          false,
        );
      }
    }
  });

  it(`computes a ${PERF_NODE_COUNT}-node/${PERF_EDGE_COUNT}-edge layout within the perf budget`, async () => {
    const { nodes, edges } = generateChainGraph(PERF_NODE_COUNT, PERF_EDGE_COUNT);
    const startedAt = performance.now();
    await computeLayout(nodes, edges, "dependency");
    const elapsedMs = performance.now() - startedAt;

    // 3x the NFR-3 300ms target — absorbs CI-machine variance. The dev-machine number
    // recorded in STATUS.md is held to the tighter 300ms bar.
    const PERF_BUDGET_MS = 900;
    expect(elapsedMs).toBeLessThanOrEqual(PERF_BUDGET_MS);
  });
});

// ---- A4 / ADR-0023: no "cone" growth, no movement on append -----------------------------------

type Edge = { source: string; target: string };

/** Overseer-style DAG: intake, then per wave k units u<k>.<i> -> checkpoint ck<k>; wave k units
 * depend on ck<k-1>. `charter` adds intake -> every unit; `skip` makes the first two units of a
 * wave also depend on a unit two waves back. Deterministic. */
function overseerGraph(waveSizes: number[], opts: { charter?: boolean; skip?: boolean } = {}) {
  const nodes: { id: string }[] = [{ id: "intake" }];
  const edges: Edge[] = [];
  waveSizes.forEach((size, w) => {
    const prev = w === 0 ? "intake" : `ck${w - 1}`;
    const ck = `ck${w}`;
    for (let i = 0; i < size; i++) {
      const id = `u${w}.${i}`;
      nodes.push({ id });
      edges.push({ source: prev, target: id });
      edges.push({ source: id, target: ck });
      if (opts.charter) edges.push({ source: "intake", target: id });
      if (opts.skip && i < 2 && w >= 2) edges.push({ source: `u${w - 2}.0`, target: id });
    }
    nodes.push({ id: ck });
  });
  return { nodes, edges };
}

const WAVES = [6, 6, 5, 5, 4, 4, 3, 3, 2, 2];
const PITCH = NODE_HEIGHT + NODE_SEP;

/** Cross-axis (y, LR) extent of each unit wave. */
function waveSpans(positions: Map<string, { x: number; y: number }>, sizes: number[]): number[] {
  return sizes.map((size, w) => {
    const ys = Array.from({ length: size }, (_, i) => positions.get(`u${w}.${i}`)!.y);
    return Math.max(...ys) + NODE_HEIGHT - Math.min(...ys);
  });
}

describe("transitiveReduction", () => {
  it("drops an edge implied by a longer path and keeps input order", () => {
    const edges = [
      { source: "a", target: "c" },
      { source: "a", target: "b" },
      { source: "b", target: "c" },
    ];
    expect(transitiveReduction(["a", "b", "c"], edges)).toEqual([edges[1], edges[2]]);
    expect(redundantEdges(["a", "b", "c"], edges)).toEqual([edges[0]]);
  });

  it("keeps parallel branches and ignores unknown endpoints", () => {
    const edges = [
      { source: "a", target: "b" },
      { source: "a", target: "c" },
      { source: "a", target: "ghost" },
    ];
    expect(transitiveReduction(["a", "b", "c"], edges)).toEqual(edges);
  });

  it("reports nothing redundant on a cycle", () => {
    const edges = [
      { source: "a", target: "b" },
      { source: "b", target: "a" },
      { source: "a", target: "c" },
      { source: "b", target: "c" },
    ];
    expect(redundantEdges(["a", "b", "c"], edges)).toEqual([]);
  });
});

describe("computeLayout: wave width and stability (A4)", () => {
  for (const [name, opts] of [
    ["plain", {}],
    ["skip", { skip: true }],
    ["charter", { charter: true }],
    ["charter+skip", { charter: true, skip: true }],
  ] as const) {
    it(`${name}: each wave spans no more than its task count justifies, and never widens`, async () => {
      const { nodes, edges } = overseerGraph(WAVES, opts);
      const { positions } = await computeLayout(nodes, edges, "dependency");
      const spans = waveSpans(positions, WAVES);
      spans.forEach((span, w) => expect(span, `wave ${w}`).toBeLessThanOrEqual(WAVES[w] * PITCH - NODE_SEP));
      for (let w = 1; w < WAVES.length; w++) {
        if (WAVES[w] <= WAVES[w - 1]) expect(spans[w], `wave ${w}`).toBeLessThanOrEqual(spans[w - 1]);
      }
    });

    it(`${name}: appending a wave moves no existing node`, async () => {
      const before = overseerGraph(WAVES, opts);
      const after = overseerGraph([...WAVES, 2], opts);
      const first = await computeLayout(before.nodes, before.edges, "dependency");
      const second = await computeLayout(after.nodes, after.edges, "dependency");
      for (const n of before.nodes) expect(second.positions.get(n.id), n.id).toEqual(first.positions.get(n.id));
    });

    it(`${name}: same graph gives the same layout`, async () => {
      const { nodes, edges } = overseerGraph(WAVES, opts);
      const a = await computeLayout(nodes, edges, "dependency");
      const b = await computeLayout(nodes, edges, "dependency");
      expect(Array.from(a.positions)).toEqual(Array.from(b.positions));
    });
  }
});
