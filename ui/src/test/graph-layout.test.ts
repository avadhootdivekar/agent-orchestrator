import { describe, expect, it } from "vitest";
import { computeLayout } from "../graph/layout";
import { NODE_HEIGHT, NODE_WIDTH } from "../graph/model";

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
