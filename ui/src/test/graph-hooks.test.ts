import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useSelectAndCenter } from "../graph/hooks";
import { NODE_HEIGHT, NODE_WIDTH } from "../graph/model";
import type { LayoutResult } from "../types";

/**
 * `useSelectAndCenter` (`T-aHktGB`). Per the TASK.md Risk note, `setCenter` isn't observable in
 * jsdom -- `useReactFlow` is mocked outright so the hook's OWN decisions (immediate center vs.
 * defer-until-relayout, the zoom floor) can be asserted via spy arguments. `vi.mock` calls are
 * hoisted above this file's imports by vitest, so `../graph/hooks` picks up the mock below.
 */

const mockReactFlow = {
  setCenter: vi.fn(),
  getZoom: vi.fn(() => 0.5),
};

vi.mock("@xyflow/react", () => ({
  useReactFlow: () => mockReactFlow,
}));

function makeLayout(positions: Record<string, { x: number; y: number }>): LayoutResult {
  return { positions: new Map(Object.entries(positions)), direction: "LR" };
}

beforeEach(() => {
  mockReactFlow.setCenter.mockClear();
  mockReactFlow.getZoom.mockClear().mockReturnValue(0.5);
});

describe("useSelectAndCenter", () => {
  it("centers immediately (zoom floor 1) and selects when the id is visible and laid out", () => {
    const layout = makeLayout({ a: { x: 100, y: 200 } });
    const onSelect = vi.fn();
    const setShowUnrelated = vi.fn();
    const { result } = renderHook(() =>
      useSelectAndCenter({ layout, isHidden: () => false, setShowUnrelated, onSelect }),
    );

    act(() => result.current("a"));

    expect(mockReactFlow.setCenter).toHaveBeenCalledWith(100 + NODE_WIDTH / 2, 200 + NODE_HEIGHT / 2, {
      zoom: 1,
    });
    expect(onSelect).toHaveBeenCalledWith("a");
    expect(setShowUnrelated).not.toHaveBeenCalled();
  });

  it("keeps the current zoom when it's already above the floor", () => {
    mockReactFlow.getZoom.mockReturnValue(2);
    const layout = makeLayout({ a: { x: 0, y: 0 } });
    const { result } = renderHook(() =>
      useSelectAndCenter({ layout, isHidden: () => false, setShowUnrelated: vi.fn(), onSelect: vi.fn() }),
    );

    act(() => result.current("a"));

    expect(mockReactFlow.setCenter).toHaveBeenCalledWith(
      NODE_WIDTH / 2,
      NODE_HEIGHT / 2,
      { zoom: 2 },
    );
  });

  it("does nothing when the id isn't in the current layout (and isn't hidden)", () => {
    const layout = makeLayout({});
    const onSelect = vi.fn();
    const { result } = renderHook(() =>
      useSelectAndCenter({ layout, isHidden: () => false, setShowUnrelated: vi.fn(), onSelect }),
    );

    act(() => result.current("missing"));

    expect(mockReactFlow.setCenter).not.toHaveBeenCalled();
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("defers centering when hidden: enables the filter, waits for the relayout, then centers", () => {
    const onSelect = vi.fn();
    const setShowUnrelated = vi.fn();
    const { result, rerender } = renderHook(
      ({ layout }: { layout: LayoutResult }) =>
        useSelectAndCenter({ layout, isHidden: (id) => id === "a", setShowUnrelated, onSelect }),
      { initialProps: { layout: makeLayout({}) } },
    );

    act(() => result.current("a"));

    // Hidden -> the filter is switched on, but nothing is centered yet (no position to center on).
    expect(setShowUnrelated).toHaveBeenCalledWith(true);
    expect(mockReactFlow.setCenter).not.toHaveBeenCalled();
    expect(onSelect).not.toHaveBeenCalled();

    // The relayout the filter flip triggered lands, now placing "a".
    rerender({ layout: makeLayout({ a: { x: 40, y: 60 } }) });

    expect(mockReactFlow.setCenter).toHaveBeenCalledWith(40 + NODE_WIDTH / 2, 60 + NODE_HEIGHT / 2, {
      zoom: 1,
    });
    expect(onSelect).toHaveBeenCalledWith("a");
  });

  it("does not re-trigger a center for an unrelated layout update once nothing is pending", () => {
    const onSelect = vi.fn();
    const { rerender } = renderHook(
      ({ layout }: { layout: LayoutResult }) =>
        useSelectAndCenter({ layout, isHidden: () => false, setShowUnrelated: vi.fn(), onSelect }),
      { initialProps: { layout: makeLayout({ a: { x: 0, y: 0 } }) } },
    );

    rerender({ layout: makeLayout({ a: { x: 5, y: 5 } }) });

    expect(mockReactFlow.setCenter).not.toHaveBeenCalled();
    expect(onSelect).not.toHaveBeenCalled();
  });
});
