import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FileBrowser } from "../components/FileBrowser";
import type { DirListing, FileContent, HtmlPreview } from "../types";

/**
 * Mode selection per `kind` (1B.2/1B.7), exercised end-to-end through FileBrowser rather
 * than re-deriving the dispatch logic in a smaller harness — this is exactly the branch that
 * decides which viewer a real file open renders into.
 */
function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const LISTING: DirListing = {
  root: "workspace",
  path: "",
  absolute: "/ws",
  entries: [
    { name: "a.py", path: "a.py", root: "workspace", is_dir: false, size: 10, modified: 0, is_symlink: false, hidden: false },
  ],
};

function baseContent(overrides: Partial<FileContent>): FileContent {
  return {
    path: "a.py",
    root: "workspace",
    size: 10,
    is_binary: false,
    truncated: false,
    text: null,
    kind: "text",
    mime: null,
    data_uri: null,
    ...overrides,
  };
}

function mockFetch(fileContent: FileContent, htmlPreview?: HtmlPreview) {
  // The fixture directory only ever has the one `a.py` entry — every test clicks that row,
  // and the server response's own `path` field (not the request's query path) is what
  // FileBrowser actually keys its kind/extension dispatch off of, so it is left untouched
  // here rather than overwritten with whatever path was requested.
  return vi.fn(async (url: string) => {
    const [path] = url.split("?");
    if (path === "/api/files") return jsonResponse(LISTING);
    if (path === "/api/files/content") return jsonResponse(fileContent);
    if (path === "/api/files/html" && htmlPreview) return jsonResponse(htmlPreview);
    return jsonResponse({ detail: "not found" }, 404);
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("FileBrowser viewer mode dispatch", () => {
  it("kind=text (non-markdown) renders CodeView directly, with no mode toggle", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch(baseContent({ kind: "text", text: "print(1)\n" })),
    );
    const { container } = render(<FileBrowser />);
    await userEvent.click(await screen.findByText("a.py"));

    // hljs splits highlighted tokens across spans, so assert on raw text content.
    await waitFor(() =>
      expect(container.querySelector(".code-lines")?.textContent).toContain("print(1)"),
    );
    expect(screen.queryByRole("tab", { name: "Preview" })).not.toBeInTheDocument();
  });

  it("kind=text + markdown extension defaults to rendered Preview, toggles to raw Source", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch(
        baseContent({ path: "notes.md", kind: "text", text: "# Heading\n" }),
      ),
    );
    render(<FileBrowser />);
    await userEvent.click(await screen.findByText("a.py"));

    // Rendered preview: a real <h1>, not the literal "# Heading" text.
    await waitFor(() => expect(screen.getByRole("heading", { name: "Heading" })).toBeInTheDocument());
    expect(screen.getByRole("tab", { name: "Preview" })).toHaveAttribute("aria-selected", "true");

    await userEvent.click(screen.getByRole("tab", { name: "Source" }));
    expect(screen.getByText("# Heading")).toBeInTheDocument();
  });

  it("kind=markup defaults to Source (raw markup), preview is a deliberate click", async () => {
    const preview: HtmlPreview = {
      path: "page.html",
      root: "workspace",
      html: "<p>rendered</p>",
      inlined: 0,
      dropped: [],
      scripts_removed: 1,
      truncated: false,
      budget_bytes: 10_000_000,
      budget_used: 0,
    };
    vi.stubGlobal(
      "fetch",
      mockFetch(
        baseContent({ path: "page.html", kind: "markup", text: "<p>hello</p>\n" }),
        preview,
      ),
    );
    const { container } = render(<FileBrowser />);
    await userEvent.click(await screen.findByText("a.py"));

    await waitFor(() => expect(screen.getByRole("tab", { name: "Source" })).toHaveAttribute("aria-selected", "true"));
    // hljs splits the markup across several highlighted spans, so assert on the raw text
    // content rather than a single exact text node.
    expect(container.querySelector(".code-lines")?.textContent).toContain("<p>hello</p>");
    expect(screen.queryByTitle(/Preview of/)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: "Preview" }));
    await waitFor(() => expect(screen.getByTitle("Preview of page.html")).toBeInTheDocument());
  });

  it("kind=image renders ImageView from data_uri, with no mode toggle or typography controls", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch(
        baseContent({
          path: "pic.png",
          kind: "image",
          mime: "image/png",
          data_uri: "data:image/png;base64,AAAA",
        }),
      ),
    );
    const { container } = render(<FileBrowser />);
    await userEvent.click(await screen.findByText("a.py"));

    const img = await screen.findByRole("img");
    expect(img).toHaveAttribute("src", "data:image/png;base64,AAAA");
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
    expect(container.querySelector(".typography-controls")).toBeNull();
  });

  it("kind=binary shows the existing metadata banner", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch(baseContent({ path: "blob.bin", kind: "binary", is_binary: true, text: null })),
    );
    render(<FileBrowser />);
    await userEvent.click(await screen.findByText("a.py"));

    await waitFor(() =>
      expect(screen.getByText(/Binary file/)).toBeInTheDocument(),
    );
  });
});

describe("FileBrowser dropped-ref rendering (markup preview)", () => {
  it("renders dropped refs as text within the viewer, never as links", async () => {
    const preview: HtmlPreview = {
      path: "page.html",
      root: "workspace",
      html: "<p>rendered</p>",
      inlined: 0,
      dropped: [{ url: "http://evil.example.com/x.png", reason: "external" }],
      scripts_removed: 0,
      truncated: false,
      budget_bytes: 10_000_000,
      budget_used: 0,
    };
    vi.stubGlobal(
      "fetch",
      mockFetch(baseContent({ path: "page.html", kind: "markup", text: "<p>hi</p>" }), preview),
    );
    render(<FileBrowser />);
    await userEvent.click(await screen.findByText("a.py"));
    await userEvent.click(await screen.findByRole("tab", { name: "Preview" }));

    const url = await screen.findByText("http://evil.example.com/x.png");
    expect(url.closest("a")).toBeNull();
    const found = within(url.closest(".html-preview")!).queryAllByRole("link");
    expect(found).toHaveLength(0);
  });
});
