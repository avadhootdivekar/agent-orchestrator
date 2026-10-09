import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GraphTab } from "../tabs/GraphTab";
import { TabActionsContext, TabActiveContext, type TabActions } from "../tabs/context";
import { TabBar } from "../tabs/TabBar";
import { OpenInNewTabButton, TabLink } from "../tabs/TabLink";
import { TaskTab } from "../tabs/TaskTab";
import { makeTab, type Tab } from "../tabs/model";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function actionsSpy(available = true) {
  const spy: TabActions = { available, navigate: vi.fn(), open: vi.fn(), retarget: vi.fn() };
  return spy;
}

describe("TabLink", () => {
  const target = { kind: "run", params: { id: "r1" } } as const;

  it("plain click navigates; ctrl/meta/middle open a background tab; shift is native", () => {
    const actions = actionsSpy();
    render(
      <TabActionsContext.Provider value={actions}>
        <TabLink target={target}>go</TabLink>
      </TabActionsContext.Provider>,
    );
    const link = screen.getByRole("link", { name: "go" });
    fireEvent.click(link);
    expect(actions.navigate).toHaveBeenCalledWith(target);
    fireEvent.click(link, { metaKey: true });
    expect(actions.open).toHaveBeenLastCalledWith(target, { activate: false });
    fireEvent(link, new MouseEvent("auxclick", { bubbles: true, cancelable: true, button: 1 }));
    expect(actions.open).toHaveBeenCalledTimes(2);
    fireEvent(link, new MouseEvent("auxclick", { bubbles: true, cancelable: true, button: 2 }));
    expect(actions.open).toHaveBeenCalledTimes(2);
    const before = (actions.navigate as ReturnType<typeof vi.fn>).mock.calls.length;
    fireEvent.click(link, { shiftKey: true });
    expect((actions.navigate as ReturnType<typeof vi.fn>).mock.calls.length).toBe(before);
  });

  it("outside the workspace shell it falls back to onPlainClick and offers no new-tab button", () => {
    const onPlain = vi.fn();
    render(
      <>
        <TabLink target={target} onPlainClick={onPlain}>
          go
        </TabLink>
        <OpenInNewTabButton target={target} label="run r1" />
      </>,
    );
    const link = screen.getByRole("link", { name: "go" });
    fireEvent.click(link, { ctrlKey: true });
    fireEvent(link, new MouseEvent("auxclick", { bubbles: true, cancelable: true, button: 1 }));
    expect(onPlain).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("an invalid target renders plain text, never a link", () => {
    render(<TabLink target={{ kind: "run", params: { id: "../../etc" } }}>bad</TabLink>);
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByText("bad")).toBeInTheDocument();
    render(<OpenInNewTabButton target={{ kind: "run", params: { id: "../x" } }} label="x" />);
    expect(screen.queryByRole("button")).toBeNull();
  });
});

describe("TabBar keyboard", () => {
  const tabs = ["usage", "new", "settings"].map((k, i) => makeTab(k, {}, `t-${i}`) as Tab);

  it("arrows move focus with a roving tabindex; activate/close/move callbacks fire", async () => {
    const onActivate = vi.fn();
    const onClose = vi.fn();
    const onMove = vi.fn();
    render(<TabBar tabs={tabs} activeId="t-0" onActivate={onActivate} onClose={onClose} onMove={onMove} />);
    const user = userEvent.setup();
    const [a, b] = screen.getAllByRole("tab");
    expect(a).toHaveAttribute("tabindex", "0");
    expect(b).toHaveAttribute("tabindex", "-1");
    a.focus();
    await user.keyboard("{ArrowRight}");
    expect(b).toHaveFocus();
    await user.keyboard("{ArrowLeft}{ArrowLeft}"); // clamps at the first tab
    expect(a).toHaveFocus();
    await user.keyboard("{Alt>}{ArrowRight}{/Alt}");
    expect(onMove).toHaveBeenCalledWith(0, 1);
    await user.click(b);
    expect(onActivate).toHaveBeenCalledWith("t-1");
    await user.click(screen.getByRole("button", { name: "Close tab Usage" }));
    expect(onClose).toHaveBeenCalledWith("t-0");
  });
});

describe("GraphTab / TaskTab", () => {
  it("GraphTab explains a backend without graph support", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        json({ summary: {}, tasks: [], graph_version: null, run_dir: "", is_live: false }),
      ),
    );
    render(<GraphTab runId="r1" />);
    expect(await screen.findByText(/does not provide a run graph/)).toBeInTheDocument();
  });

  it("GraphTab and TaskTab surface API errors instead of crashing", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ detail: "run not found: r1" }, 404)));
    render(
      <>
        <GraphTab runId="r1" />
        <TaskTab runId="r1" taskId="t" />
      </>,
    );
    expect(await screen.findAllByRole("alert")).toHaveLength(2);
  });

  it("is inert while its tab is inactive (no fetch at all)", () => {
    const fetchMock = vi.fn(async () => json({}));
    vi.stubGlobal("fetch", fetchMock);
    render(
      <TabActiveContext.Provider value={false}>
        <GraphTab runId="r1" />
      </TabActiveContext.Provider>,
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
