import { describe, expect, it } from "vitest";
import { BADGE_LABELS, badgesFor } from "../graph/TaskNode";
import { editedTargetIds, isEditedEdge } from "../graph/RunGraph";
import type { GraphEdit, ViewEdge, ViewNode } from "../types";

function node(origin: string): ViewNode {
  return {
    id: "n", label: "n", label_sanitized: false, origin, parent_task_id: null, route: null,
    loop_id: null, iteration: null, spawn_depth: 0, children_count: 0, is_emitter: false,
    is_router: false, is_loop_gate: false, exec_ordinal: null, missing: false, stat: null,
  };
}

describe("sentinel-origin graph styling (T-Sn6Brr AC5)", () => {
  it("gives a sentinel-origin node its own badge, not the unknown-origin catch-all", () => {
    const texts = badgesFor(node("sentinel")).map((b) => b.text);
    expect(texts).toContain(BADGE_LABELS.sentinel);
    expect(texts.some((t) => t.endsWith(" origin"))).toBe(false);
  });

  it("injected and static nodes are unchanged", () => {
    expect(badgesFor(node("static"))).toEqual([]);
    expect(badgesFor(node("injected")).map((b) => b.text)).toEqual([BADGE_LABELS.injected]);
  });
});

describe("edited-edge styling (graph_edits)", () => {
  const edits: GraphEdit[] = [
    { op: "set_edges", task_id: "b", dependencies: ["a"], inputs: null, value: null, source: "sentinel-g01", at: "" },
    { op: "priority", task_id: "c", dependencies: null, inputs: null, value: 5, source: "ctl:x", at: "" },
  ];
  const dep = (target: string): ViewEdge => ({
    id: `dep:a->${target}`, source: "a", target, kind: "explicit", via: null, set: "dependency",
  });

  it("only set_edges targets count as edited", () => {
    expect([...editedTargetIds(edits)]).toEqual(["b"]);
    expect(editedTargetIds(undefined).size).toBe(0);
  });

  it("marks dependency edges into an edited target, not others or spawn edges", () => {
    const targets = editedTargetIds(edits);
    expect(isEditedEdge(dep("b"), targets)).toBe(true);
    expect(isEditedEdge(dep("c"), targets)).toBe(false);
    const spawn: ViewEdge = { id: "spawn:a->b", source: "a", target: "b", origin: "sentinel", loop_id: null, iteration: null, set: "spawn" };
    expect(isEditedEdge(spawn, targets)).toBe(false);
  });
});
