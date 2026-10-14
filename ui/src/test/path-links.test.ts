import { describe, expect, it } from "vitest";
import { findPathCandidates, normalizePath } from "../tabs/pathLinks";

const WS = "/home/u/ws";
const paths = (text: string, root: string | null = WS) =>
  findPathCandidates(text, root).map((c) => c.path);

describe("findPathCandidates (A5)", () => {
  it("detects relative paths, ./ prefixes and bare known-extension files", () => {
    expect(paths("see docs-md/a.md and ./out/x.json then README.md")).toEqual([
      "docs-md/a.md",
      "out/x.json",
      "README.md",
    ]);
  });

  it("drops trailing punctuation and :line suffixes, and reports exact spans", () => {
    const text = "(src/m.py:12:3), 'a/b.txt'.";
    const found = findPathCandidates(text, WS);
    expect(found.map((c) => c.path)).toEqual(["src/m.py", "a/b.txt"]);
    for (const c of found) expect(text.slice(c.start, c.end)).toBe(c.raw);
  });

  it("rewrites absolute paths inside the workspace and drops those outside it", () => {
    expect(paths(`${WS}/a/b.md`)).toEqual(["a/b.md"]);
    expect(paths("/etc/passwd")).toEqual([]);
    expect(paths(`${WS}-evil/x.md`)).toEqual([]);
    expect(paths(`${WS}/a.md`, null)).toEqual([]);
  });

  it("ignores urls, schemes, home paths, bare words and version numbers", () => {
    expect(paths("http://host/x/y.md https://a.com/b.md file:///etc/x.md")).toEqual([]);
    expect(paths("javascript:alert(1) ~/secret/k.md //host/share/f.md")).toEqual([]);
    expect(paths("hello world v1.2.3 1.5 done")).toEqual([]);
  });

  it("drops candidates with .. segments, control/bidi chars or oversize length", () => {
    expect(paths("../../etc/passwd a/../../b.md")).toEqual([]);
    expect(paths("a/‮b.md")).toEqual([]);
    expect(normalizePath("a".repeat(1100) + "/x.md", WS)).toBeNull();
  });

  it("caps candidates per text block", () => {
    const text = Array.from({ length: 300 }, (_, i) => `d/f${i}.md`).join(" ");
    expect(findPathCandidates(text, WS)).toHaveLength(200);
  });
});
