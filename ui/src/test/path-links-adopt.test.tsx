import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PromptPanel } from "../components/PromptPanel";
import { QUICK_LINKS_MAX, QuickLinks } from "../components/QuickLinks";
import { RunLog } from "../components/RunLog";
import { RunSummaryPanel } from "../components/RunSummaryPanel";
import { MarkdownView } from "../components/viewer/MarkdownView";
import { TabActionsContext, type TabActions } from "../tabs/context";
import { resetPathProbeCache } from "../tabs/usePathProbe";
import type { RunLiveSummary, RunPrompt } from "../types";

const WS = "/home/u/ws";
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

/** Fake server: `status` maps path -> probe status (default missing). Records probed batches. */
function installServer(status: Record<string, string>) {
  const probes: string[][] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/workspace"))
        return json({ workspace_root: WS, roots: [] });
      if (url.endsWith("/files/resolve")) {
        const { paths } = JSON.parse(init?.body as string) as {
          paths: string[];
        };
        probes.push(paths);
        return json({
          results: paths.map((path) => ({
            path,
            status: status[path] ?? "missing",
          })),
        });
      }
      return json({}, 404);
    }),
  );
  return probes;
}

function renderWith(node: React.ReactNode, actions?: TabActions) {
  const value = actions ?? {
    available: true,
    navigate: vi.fn(),
    open: vi.fn(),
    retarget: vi.fn(),
  };
  const view = render(
    <TabActionsContext.Provider value={value}>
      {node}
    </TabActionsContext.Provider>,
  );
  return { actions: value, ...view };
}

const prompt = (text: string, path = "wf/prompt.md"): RunPrompt => ({
  text,
  truncated: false,
  chars: text.length,
  source: "workflow-file",
  path,
  sha256: "a".repeat(64),
  captured_at: "2026-01-01T00:00:00Z",
});

beforeEach(() => resetPathProbeCache());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("PromptPanel path links (A5)", () => {
  it("links verified paths in the prompt text and its source path; others stay plain", async () => {
    installServer({
      "docs/a.md": "file",
      "wf/prompt.md": "file",
      "x/secret.md": "denied",
    });
    renderWith(
      <PromptPanel
        prompt={prompt("read docs/a.md then x/secret.md and ../../etc/passwd")}
      />,
    );
    expect(await screen.findByRole("link", { name: "docs/a.md" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "wf/prompt.md" })).toBeTruthy();
    expect(screen.getAllByRole("link")).toHaveLength(2);
    expect(
      document
        .querySelector("pre.prompt-text")
        ?.textContent?.replaceAll("⧉", ""),
    ).toBe("read docs/a.md then x/secret.md and ../../etc/passwd");
  });

  it("never probes traversal candidates", async () => {
    const probes = installServer({});
    renderWith(
      <PromptPanel
        prompt={prompt("see ../../etc/passwd and http://x.io/a.md")}
      />,
    );
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(probes.flat().some((p) => p.includes(".."))).toBe(false);
    expect(probes.flat().some((p) => p.includes("x.io"))).toBe(false);
  });
});

describe("RunSummaryPanel path links (A5)", () => {
  it("links verified paths", async () => {
    installServer({ "out/report.md": "file" });
    const summary = {
      available: true,
      text: "wrote out/report.md and out/none.md",
      meta: null,
    } as unknown as RunLiveSummary;
    renderWith(<RunSummaryPanel summary={summary} />);
    expect(
      await screen.findByRole("link", { name: "out/report.md" }),
    ).toBeTruthy();
    expect(screen.getAllByRole("link")).toHaveLength(1);
  });
});

describe("RunLog path links (A5)", () => {
  it("links verified paths, keeps the log text intact, degrades on missing", async () => {
    installServer({ "outputs/x.json": "file" });
    renderWith(<RunLog text={"[ok] outputs/x.json\n[fail] outputs/y.json"} />);
    expect(
      await screen.findByRole("link", { name: "outputs/x.json" }),
    ).toBeTruthy();
    expect(screen.getAllByRole("link")).toHaveLength(1);
    expect(
      document.querySelector("pre.code")?.textContent?.replaceAll("⧉", ""),
    ).toBe("[ok] outputs/x.json\n[fail] outputs/y.json");
  });
});

describe("MarkdownView path links (A5)", () => {
  it("links verified paths in prose and inline code, not in fenced code or existing links", async () => {
    installServer({
      "a/ok.md": "file",
      "a/dir": "dir",
      "b/in-fence.md": "file",
      "c/link.md": "file",
    });
    const source =
      "See a/ok.md and `a/dir` plus [c/link.md](https://example.com).\n\n```\nb/in-fence.md\n```\n";
    const { container, actions } = renderWith(
      <MarkdownView source={source} filePath="docs/r.md" root="workspace" />,
    );
    await waitFor(() =>
      expect(container.querySelectorAll("a[data-ao-path]")).toHaveLength(2),
    );
    const link = container.querySelector(
      'a[data-ao-path="a/ok.md"]',
    ) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("#/file?path=a%2Fok.md");
    expect(
      container.querySelector('a[data-ao-path="b/in-fence.md"]'),
    ).toBeNull();
    expect(
      container
        .querySelector('a[href="https://example.com"]')
        ?.hasAttribute("data-ao-path"),
    ).toBe(false);

    fireEvent.click(link);
    expect(actions.navigate).toHaveBeenCalledWith({
      kind: "file",
      params: { path: "a/ok.md" },
    });
    fireEvent.click(link, { ctrlKey: true });
    expect(actions.open).toHaveBeenCalledWith(
      { kind: "file", params: { path: "a/ok.md" } },
      { activate: false },
    );
  });

  it("leaves missing/denied/traversal paths as plain text and the sanitizer intact", async () => {
    installServer({ "x/secret.md": "denied" });
    const { container } = renderWith(
      <MarkdownView
        source={
          "x/secret.md a/gone.md ../../etc/passwd <script>alert(1)</script>"
        }
        filePath="r.md"
        root="workspace"
      />,
    );
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    expect(container.querySelector("a")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container.textContent).toContain(
      "x/secret.md a/gone.md ../../etc/passwd",
    );
  });
});

describe("QuickLinks (A5)", () => {
  it("lists verified files as links and unverified ones as plain text", async () => {
    installServer({ "run/workflow.json": "file", "run/outputs/o.md": "file" });
    renderWith(
      <QuickLinks
        links={[
          { path: "run/workflow.json", label: "workflow.json" },
          { path: "run/prompt.md" },
          { path: "run/outputs/o.md" },
          { path: "run/outputs/o.md" },
        ]}
      />,
    );
    expect(
      await screen.findByRole("link", { name: "workflow.json" }),
    ).toBeTruthy();
    expect(screen.getAllByRole("link")).toHaveLength(2);
    expect(screen.getAllByRole("listitem")).toHaveLength(3); // deduped
    expect(screen.getByText("run/prompt.md").closest("a")).toBeNull();
  });

  it("renders nothing for an empty list and caps long lists", () => {
    installServer({});
    const { container } = renderWith(<QuickLinks links={[]} />);
    expect(container.querySelector("nav")).toBeNull();
    cleanup();
    const many = Array.from({ length: QUICK_LINKS_MAX + 10 }, (_, i) => ({
      path: `o/f${i}.md`,
    }));
    renderWith(<QuickLinks links={many} />);
    expect(screen.getAllByRole("listitem")).toHaveLength(QUICK_LINKS_MAX);
  });

  it("does not link traversal paths even if the caller passes them", async () => {
    const probes = installServer({});
    renderWith(<QuickLinks links={[{ path: "../../etc/passwd" }]} />);
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByRole("link")).toBeNull();
    expect(probes.flat().some((p) => p.includes(".."))).toBe(false);
  });
});
