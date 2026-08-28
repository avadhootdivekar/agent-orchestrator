import { render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MARKDOWN_MAX_IMAGES, MarkdownView } from "../components/viewer/MarkdownView";
import type { FileContent } from "../types";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function imageContent(path: string): FileContent {
  return {
    path,
    root: "workspace",
    size: 42,
    is_binary: false,
    truncated: false,
    text: null,
    kind: "image",
    mime: "image/png",
    data_uri: "data:image/png;base64,AAAA",
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("MarkdownView sanitization", () => {
  it("strips a raw <script> tag from the source", () => {
    const source = "Hello\n\n<script>alert(1)</script>\n\nWorld";
    const { container } = render(
      <MarkdownView source={source} filePath="docs/readme.md" root="workspace" />,
    );
    expect(container.querySelector("script")).toBeNull();
    expect(container.textContent).toContain("Hello");
    expect(container.textContent).toContain("World");
  });

  it("strips a javascript: link href", () => {
    const source = "[click me](javascript:alert(1))";
    const { container } = render(
      <MarkdownView source={source} filePath="docs/readme.md" root="workspace" />,
    );
    const anchor = container.querySelector("a");
    // DOMPurify drops the whole attribute rather than leaving a live javascript: URI.
    expect(anchor?.getAttribute("href") ?? null).not.toBe("javascript:alert(1)");
  });

  it("strips an onerror attribute from a raw <img>", () => {
    const source = '<img src="x" onerror="alert(1)">';
    const { container } = render(
      <MarkdownView source={source} filePath="docs/readme.md" root="workspace" />,
    );
    const img = container.querySelector("img");
    expect(img?.getAttribute("onerror")).toBeNull();
  });

  it("highlights fenced code blocks via the same capped hljs path as CodeView", () => {
    const source = "```python\ndef foo():\n    return 1\n```";
    const { container } = render(
      <MarkdownView source={source} filePath="docs/readme.md" root="workspace" />,
    );
    expect(container.innerHTML).toContain("hljs-");
  });

  it("opens external links in a new tab without a live opener reference", async () => {
    const source = "[docs](https://example.com/docs)";
    const { container } = render(
      <MarkdownView source={source} filePath="docs/readme.md" root="workspace" />,
    );
    await waitFor(() => {
      const anchor = container.querySelector('a[href="https://example.com/docs"]');
      expect(anchor?.getAttribute("target")).toBe("_blank");
      expect(anchor?.getAttribute("rel")).toBe("noopener noreferrer");
    });
  });
});

describe("MarkdownView relative image resolution", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const [path, query] = url.split("?");
        const params = new URLSearchParams(query);
        if (path === "/api/files/content") {
          const requested = params.get("path");
          if (requested === "docs/sibling.png") return jsonResponse(imageContent(requested));
          return jsonResponse({ detail: "not found" }, 404);
        }
        return jsonResponse({}, 404);
      }),
    );
  });

  it("substitutes the resolved data_uri for a relative image ref", async () => {
    const source = "![alt text](sibling.png)";
    const { container } = render(
      <MarkdownView source={source} filePath="docs/readme.md" root="workspace" />,
    );
    await waitFor(() => {
      const img = container.querySelector("img");
      expect(img?.getAttribute("src")).toBe("data:image/png;base64,AAAA");
    });
  });

  it("never lets the browser fetch the raw relative ref directly", () => {
    const source = "![alt text](sibling.png)";
    const { container } = render(
      <MarkdownView source={source} filePath="docs/readme.md" root="workspace" />,
    );
    // Before resolution completes, the <img> must never carry the bogus relative path as a
    // real `src` (that would fire a same-origin request against a nonsense URL).
    const img = container.querySelector("img");
    expect(img?.getAttribute("src")).not.toBe("sibling.png");
  });

  it("renders a broken-image note (not a raw URL) for a ref that 404s", async () => {
    const source = "![missing](does-not-exist.png)";
    const { container } = render(
      <MarkdownView source={source} filePath="docs/readme.md" root="workspace" />,
    );
    await waitFor(() => {
      expect(container.querySelector(".broken-image")).not.toBeNull();
    });
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).not.toContain("does-not-exist.png");
  });

  it("never issues more than MARKDOWN_MAX_IMAGES resolution requests for one file", async () => {
    const fetchSpy = vi.mocked(fetch);
    const overflow = MARKDOWN_MAX_IMAGES + 5;
    const source = Array.from(
      { length: overflow },
      (_, i) => `![img ${i}](sibling-${i}.png)`,
    ).join("\n\n");

    const { container } = render(
      <MarkdownView source={source} filePath="docs/readme.md" root="workspace" />,
    );

    await waitFor(() => {
      // Every ref beyond the cap is marked broken immediately (synchronously, no fetch);
      // the ones within the cap are still resolving against the mocked fetch above (which
      // 404s for anything but "docs/sibling.png"), so they end up broken too — the
      // assertion that matters here is the request count, not the final broken/ok mix.
      expect(container.querySelectorAll(".broken-image, img").length).toBe(overflow);
    });

    const readCalls = fetchSpy.mock.calls.filter(([url]) =>
      String(url).startsWith("/api/files/content"),
    );
    expect(readCalls.length).toBeLessThanOrEqual(MARKDOWN_MAX_IMAGES);
  });

  it("sends a ../-containing ref as a plain joined path (no client-side normalization) and renders the server's rejection as inert text", async () => {
    const fetchSpy = vi.fn(async (url: string) => {
      const [path] = url.split("?");
      if (path === "/api/files/content") {
        // The server is the sole containment authority (HLD 1B.4/5.3) — it rejects the
        // escaping ref itself; the client must not have pre-resolved/popped the ".." first.
        return jsonResponse({ detail: "outside workspace root" }, 403);
      }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchSpy);

    const source = "![alt](../secrets/passwd.txt)";
    const { container } = render(
      <MarkdownView source={source} filePath="docs/sub/readme.md" root="workspace" />,
    );

    await waitFor(() => {
      expect(container.querySelector(".broken-image")).not.toBeNull();
    });
    expect(container.querySelector("img")).toBeNull();
    // The rejection renders as inert text, never the raw path/URL.
    expect(container.textContent).not.toContain("../secrets/passwd.txt");

    // The client sent the plain joined string (base dir + ref), unresolved — proving there is
    // no second path resolver popping ".." segments on the client.
    const [requestedUrl] = fetchSpy.mock.calls[0];
    const requestedPath = new URLSearchParams(String(requestedUrl).split("?")[1]).get("path");
    expect(requestedPath).toBe("docs/sub/../secrets/passwd.txt");
  });
});
