import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FileBrowser } from "../components/FileBrowser";
import { TabActionsContext, type TabActions } from "../tabs/context";

const json = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });

const entry = (name: string) => ({
  name, path: name, root: "ws", is_dir: false, size: 3, modified: 0, is_symlink: false, hidden: false,
});

function installFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/api/files/content")) {
        return json({ path: "a.py", root: "ws", size: 3, is_binary: false, truncated: false, text: "x", kind: "text", mime: null, data_uri: null });
      }
      return json({ root: "ws", path: "", absolute: "/ws", entries: [entry("a.py")] });
    }),
  );
}

function setup(available = true) {
  installFetch();
  const actions: TabActions = { available, navigate: vi.fn(), open: vi.fn(), retarget: vi.fn() };
  render(
    <TabActionsContext.Provider value={actions}>
      <FileBrowser />
    </TabActionsContext.Provider>,
  );
  return actions;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("FileBrowser file rows: optional new tab (A3)", () => {
  it("plain click views the file in place and opens no tab", async () => {
    const actions = setup();
    await userEvent.setup().click(await screen.findByRole("button", { name: /a\.py/ }));
    await screen.findByText("x");
    expect(actions.open).not.toHaveBeenCalled();
    expect(actions.navigate).not.toHaveBeenCalled();
  });

  it("ctrl/cmd-click and middle-click open (and focus) a file tab", async () => {
    const actions = setup();
    const row = await screen.findByRole("button", { name: /a\.py/ });
    fireEvent.click(row, { ctrlKey: true });
    fireEvent.click(row, { metaKey: true });
    fireEvent(row, new MouseEvent("auxclick", { bubbles: true, cancelable: true, button: 1 }));
    expect(actions.open).toHaveBeenCalledTimes(3);
    expect(actions.open).toHaveBeenLastCalledWith(
      { kind: "file", params: { path: "a.py" } },
      { activate: true },
    );
  });

  it("outside the workspace shell, modifier-click falls back to viewing in place", async () => {
    const actions = setup(false);
    fireEvent.click(await screen.findByRole("button", { name: /a\.py/ }), { ctrlKey: true });
    await screen.findByText("x");
    expect(actions.open).not.toHaveBeenCalled();
  });
});
