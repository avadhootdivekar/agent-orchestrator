import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { HtmlPreview } from "../components/viewer/HtmlPreview";
import type { HtmlPreview as HtmlPreviewData } from "../types";

const BASE_PREVIEW: HtmlPreviewData = {
  path: "page.html",
  root: "workspace",
  html: "<p>hello</p>",
  inlined: 2,
  dropped: [],
  scripts_removed: 3,
  truncated: false,
  budget_bytes: 10_000_000,
  budget_used: 100,
};

describe("HtmlPreview", () => {
  it("renders the iframe with an empty sandbox — no allow-scripts, no allow-same-origin", () => {
    const { container } = render(<HtmlPreview preview={BASE_PREVIEW} />);
    const iframe = container.querySelector("iframe");
    expect(iframe).not.toBeNull();
    const sandbox = iframe!.getAttribute("sandbox");
    // Setting BOTH allow-scripts and allow-same-origin together defeats the sandbox
    // entirely, so this must never regress to a non-empty value containing either token.
    expect(sandbox).toBe("");
    expect(sandbox).not.toContain("allow-scripts");
    expect(sandbox).not.toContain("allow-same-origin");
  });

  it("sets referrerPolicy=no-referrer and srcDoc from the server's sanitized html", () => {
    const { container } = render(<HtmlPreview preview={BASE_PREVIEW} />);
    const iframe = container.querySelector("iframe")!;
    expect(iframe.getAttribute("referrerpolicy")).toBe("no-referrer");
    expect(iframe.getAttribute("srcdoc")).toContain("<p>hello</p>");
    expect(iframe.getAttribute("srcdoc")).toContain("Content-Security-Policy");
  });

  it("surfaces scripts_removed and inlined counts", () => {
    const { getByText } = render(<HtmlPreview preview={BASE_PREVIEW} />);
    expect(getByText(/3 scripts removed/)).toBeInTheDocument();
    expect(getByText(/2 assets inlined/)).toBeInTheDocument();
  });

  it("renders dropped refs as inert text, grouped by reason, never as links", () => {
    const preview: HtmlPreviewData = {
      ...BASE_PREVIEW,
      dropped: [
        { url: "http://evil.example.com/track.png", reason: "external" },
        { url: "../../../etc/passwd", reason: "outside-root" },
      ],
    };
    const { container, getByText } = render(<HtmlPreview preview={preview} />);
    expect(getByText(/2 reference\(s\) dropped/)).toBeInTheDocument();
    expect(getByText("http://evil.example.com/track.png")).toBeInTheDocument();
    expect(getByText("../../../etc/passwd")).toBeInTheDocument();
    // Never rendered as a clickable reference to "somewhere else".
    const anchors = Array.from(container.querySelectorAll("a"));
    expect(anchors.some((a) => a.textContent?.includes("evil.example.com"))).toBe(false);
    expect(anchors.some((a) => a.getAttribute("href")?.includes("evil.example.com"))).toBe(
      false,
    );
  });

  it("omits the dropped-refs panel when nothing was dropped", () => {
    const { container } = render(<HtmlPreview preview={BASE_PREVIEW} />);
    expect(container.querySelector(".html-preview-dropped")).toBeNull();
  });
});
