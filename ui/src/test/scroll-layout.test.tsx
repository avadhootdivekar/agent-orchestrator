import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
// @ts-expect-error -- no @types/node in the UI package
import { readFileSync } from "node:fs";
import { App } from "../App";
import { ScrollPanel } from "../tabs/ScrollPanel";

// vitest.config sets `css: false`, which blanks stylesheet imports (incl. `?raw`), so read the
// source file directly. The UI has no @types/node (hence the ts-expect-error imports).
const css = readFileSync("src/styles.css", "utf8"); // vitest runs from ui/;

/** Body of the first top-level rule for `selector` (outside any @media block). */
function rule(selector: string): string {
  const m = css.match(new RegExp(`^${selector.replace(/[.]/g, "\\.")}\\s*\\{([^}]*)\\}`, "m"));
  if (!m) throw new Error(`no rule for ${selector}`);
  return m[1];
}

describe("fixed-viewport shell (CSS contract)", () => {
  it("pins the app to the viewport with no page-level overflow", () => {
    const app = rule(".app");
    expect(app).toMatch(/height:\s*100vh/);
    expect(app).toMatch(/overflow:\s*hidden/);
    expect(app).not.toMatch(/min-height/);
  });

  it("keeps the tab bar fixed and makes each panel its own scroll container", () => {
    expect(rule(".tabbar")).toMatch(/flex:\s*none/);
    expect(rule(".main")).toMatch(/flex-direction:\s*column/);
    expect(rule(".main")).toMatch(/min-height:\s*0/);
    const panel = rule(".tab-panel");
    expect(panel).toMatch(/overflow-y:\s*auto/);
    expect(panel).toMatch(/min-height:\s*0/);
  });

  it("falls back to page scroll only at <=900px", () => {
    const media = css.match(/@media \(max-width: 900px\) \{\s*\.app \{([^}]*)\}/);
    expect(media?.[1]).toMatch(/overflow:\s*visible/);
  });
});

describe("ScrollPanel", () => {
  afterEach(cleanup);

  it("restores the scroll offset when a hidden panel is shown again", () => {
    const { rerender } = render(
      <ScrollPanel id="p" labelledBy="t" hidden={false}>
        x
      </ScrollPanel>,
    );
    const panel = screen.getByRole("tabpanel");
    panel.scrollTop = 120;
    fireEvent.scroll(panel);

    rerender(
      <ScrollPanel id="p" labelledBy="t" hidden>
        x
      </ScrollPanel>,
    );
    panel.scrollTop = 0; // what display:none does to the offset
    fireEvent.scroll(panel); // must not overwrite the remembered offset while hidden

    rerender(
      <ScrollPanel id="p" labelledBy="t" hidden={false}>
        x
      </ScrollPanel>,
    );
    expect(screen.getByRole("tabpanel").scrollTop).toBe(120);
  });
});

describe("App tab switching keeps per-tab scroll", () => {
  beforeEach(() => {
    localStorage.clear();
    window.location.hash = "";
    // Never-resolving fetches: panels render their loading state, which is all this needs.
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("restores each tab's offset and keeps the tab bar outside the scrolling panels", async () => {
    render(<App />);
    await act(async () => {});
    const tablist = screen.getByRole("tablist");
    expect(tablist.closest(".tab-panel")).toBeNull();

    const first = screen.getAllByRole("tabpanel")[0];
    first.scrollTop = 75;
    fireEvent.scroll(first);

    await act(async () => {
      screen.getByRole("button", { name: /Usage/ }).click();
    });
    const panels = screen.getAllByRole("tabpanel", { hidden: true });
    expect(panels.length).toBeGreaterThan(1);
    first.scrollTop = 0; // display:none discards it in a real browser

    await act(async () => {
      screen.getAllByRole("tab")[0].click();
    });
    expect(first.scrollTop).toBe(75);
  });
});
