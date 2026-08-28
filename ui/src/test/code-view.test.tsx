import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CodeView, HIGHLIGHT_MAX_BYTES, highlightCode } from "../components/viewer/CodeView";

describe("highlightCode", () => {
  it("highlights a recognized language via hljs (span markup with hljs- classes)", () => {
    const { html, capped, language } = highlightCode("def foo():\n    return 1\n", "python");
    expect(capped).toBe(false);
    expect(language).toBe("python");
    expect(html).toContain("hljs-");
  });

  it("falls back to escaped plain text for an unregistered language", () => {
    const { html, capped } = highlightCode("<b>not html-escaped by hljs</b>", "brainfuck");
    expect(capped).toBe(false);
    expect(html).not.toContain("hljs-");
    expect(html).toContain("&lt;b&gt;");
  });

  it("caps highlighting past HIGHLIGHT_MAX_BYTES and renders escaped plain text instead", () => {
    // Adversarial-input guard (1B.3): a huge file must never reach the hljs regex engine.
    const huge = "x".repeat(HIGHLIGHT_MAX_BYTES + 1);
    const { html, capped } = highlightCode(huge, "python");
    expect(capped).toBe(true);
    expect(html).not.toContain("hljs-");
    expect(html).toContain("x".repeat(10));
  });

  it("never calls hljs on content within the cap for a recognized language, and stays under it for the boundary case", () => {
    const atLimit = "y".repeat(HIGHLIGHT_MAX_BYTES);
    const { capped } = highlightCode(atLimit, "python");
    expect(capped).toBe(false);
  });
});

describe("CodeView", () => {
  it("renders one line-numbered list item per source line, with no separate number text", () => {
    const { container } = render(<CodeView code={"a\nb\nc"} language="text" />);
    const items = container.querySelectorAll(".code-lines li");
    expect(items).toHaveLength(3);
    // Line numbers come from the native <li> marker (CSS ::marker), never from text
    // rendered in the same node as the code — so each item's *text* content is exactly the
    // code on that line, nothing more.
    expect(items[0].textContent).toBe("a");
    expect(items[1].textContent).toBe("b");
    expect(items[2].textContent).toBe("c");
  });

  it("shows a note and skips highlighting once content exceeds the byte cap", () => {
    const huge = "z".repeat(HIGHLIGHT_MAX_BYTES + 100);
    const { container, getByText } = render(<CodeView code={huge} language="python" />);
    expect(getByText(/exceeds/i)).toBeInTheDocument();
    expect(container.querySelector(".hljs-keyword")).toBeNull();
  });

  it("highlights known languages below the cap", () => {
    const { container } = render(
      <CodeView code={"def foo():\n    pass\n"} language="python" />,
    );
    expect(container.querySelector(".code-lines")).not.toBeNull();
    expect(container.innerHTML).toContain("hljs-");
  });
});
