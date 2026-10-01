import "@testing-library/jest-dom/vitest";

/**
 * React Flow jsdom shims (T-adVpTj, AC-6). jsdom implements neither `ResizeObserver` nor
 * `DOMMatrixReadOnly`, and reports every element's `offsetWidth`/`offsetHeight` as `0` — React
 * Flow needs real values for viewport/fitView math, or it silently renders nothing. This is
 * React Flow's own documented jsdom testing setup (https://reactflow.dev/learn/advanced-use/testing),
 * added once here rather than per-test so every future `RunGraph.tsx`/`TaskNode.tsx` test
 * (T-OjTS8O and friends) gets it for free.
 */

class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
window.ResizeObserver = window.ResizeObserver ?? (ResizeObserverStub as unknown as typeof ResizeObserver);

// React Flow reads `m22` off a `DOMMatrixReadOnly` to derive the current zoom level. jsdom has
// no CSS transform engine, so a fixed identity-scale stub is enough — tests assert layout and
// data flow, not real pixel-accurate zoom.
class DOMMatrixReadOnlyStub {
  m22 = 1;
}
window.DOMMatrixReadOnly =
  window.DOMMatrixReadOnly ?? (DOMMatrixReadOnlyStub as unknown as typeof DOMMatrixReadOnly);

// jsdom leaves every element at 0x0. React Flow nodes carry an explicit inline `width`/`height`
// style (see model.ts NODE_WIDTH/NODE_HEIGHT), so reading it back gives a real, non-zero size;
// anything without one (e.g. a plain test div) falls back to 1, never 0.
Object.defineProperties(window.HTMLElement.prototype, {
  offsetHeight: {
    get() {
      return Number.parseFloat((this as HTMLElement).style.height) || 1;
    },
    configurable: true,
  },
  offsetWidth: {
    get() {
      return Number.parseFloat((this as HTMLElement).style.width) || 1;
    },
    configurable: true,
  },
});
