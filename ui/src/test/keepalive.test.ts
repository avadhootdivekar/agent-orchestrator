import { act, cleanup, fireEvent, render, renderHook } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthGate } from "../auth/AuthGate";
import { KEEPALIVE_MIN_INTERVAL_MS } from "../auth/constants";
import { useKeepalive } from "../auth/useKeepalive";
import { AUTHENTICATED_STATUS, ANONYMOUS_STATUS, callsTo, headerOf, json, mockFetch, resetAuthState, status } from "./fixtures/auth";

const KEEPALIVE = "/api/auth/keepalive";
const PROOF = "P-live";
const FIXED_NOW = new Date("2026-10-05T12:00:00Z");
const KEEPALIVE_BODY = {
  idle_expires_at: "2026-10-05T12:30:00Z",
  absolute_expires_at: "2026-10-06T00:00:00Z",
  idle_timeout_seconds: 1800,
};

function setVisibility(state: "visible" | "hidden") {
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
}

beforeEach(() => {
  resetAuthState();
  localStorage.setItem("ao-session-proof", PROOF);
  setVisibility("visible");
  vi.useFakeTimers();
  vi.setSystemTime(FIXED_NOW);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
  delete (document as unknown as Record<string, unknown>).visibilityState;
});

function keepaliveFetch() {
  return mockFetch((url) => (url === KEEPALIVE ? json(KEEPALIVE_BODY) : json({})));
}

/** Advance fake time in steps, firing `fire` after each step. */
async function everyStep(totalMs: number, stepMs: number, fire: () => void) {
  for (let elapsed = 0; elapsed < totalMs; elapsed += stepMs) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(stepMs);
    });
    fire();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
  }
}

describe("useKeepalive", () => {
  it("pointer events every 10 s for 3 minutes -> exactly 3 POSTs, each with the proof header", async () => {
    const fetchMock = keepaliveFetch();
    renderHook(() => useKeepalive(true));
    await everyStep(180_000, 10_000, () => fireEvent.pointerDown(window));
    const posts = callsTo(fetchMock, KEEPALIVE);
    expect(posts).toHaveLength(3);
    for (const [, init] of posts) {
      expect(init?.method).toBe("POST");
      expect(headerOf(init, "X-AO-Session-Proof")).toBe(PROOF);
      expect(init?.body).toBeUndefined();
    }
  });

  it("every activity event type counts, and the visible-again event counts too", async () => {
    const fetchMock = keepaliveFetch();
    renderHook(() => useKeepalive(true));
    const events: Array<() => void> = [
      () => fireEvent.pointerDown(window),
      () => fireEvent.keyDown(window, { key: "a" }),
      () => fireEvent.wheel(window),
      () => fireEvent.touchStart(window),
      () => fireEvent(document, new Event("visibilitychange")),
    ];
    for (const fire of events) {
      await everyStep(KEEPALIVE_MIN_INTERVAL_MS, KEEPALIVE_MIN_INTERVAL_MS, fire);
    }
    expect(callsTo(fetchMock, KEEPALIVE)).toHaveLength(events.length);
  });

  it("a burst of activity inside the minimum interval sends once", async () => {
    const fetchMock = keepaliveFetch();
    renderHook(() => useKeepalive(true));
    for (let i = 0; i < 20; i += 1) fireEvent.pointerDown(window);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(KEEPALIVE_MIN_INTERVAL_MS - 1);
    });
    fireEvent.pointerDown(window);
    expect(callsTo(fetchMock, KEEPALIVE)).toHaveLength(1);
  });

  it("sends nothing from a hidden tab", async () => {
    const fetchMock = keepaliveFetch();
    setVisibility("hidden");
    renderHook(() => useKeepalive(true));
    await everyStep(180_000, 10_000, () => fireEvent.pointerDown(window));
    fireEvent(document, new Event("visibilitychange"));
    expect(callsTo(fetchMock, KEEPALIVE)).toHaveLength(0);
  });

  it("schedules no timer while idle (event-driven only)", async () => {
    const fetchMock = keepaliveFetch();
    renderHook(() => useKeepalive(true));
    expect(vi.getTimerCount()).toBe(0);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10 * KEEPALIVE_MIN_INTERVAL_MS);
    });
    expect(callsTo(fetchMock, KEEPALIVE)).toHaveLength(0);
    fireEvent.pointerDown(window);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(vi.getTimerCount()).toBe(0);
  });

  it("inactive: no listeners, and unmounting removes them", () => {
    const fetchMock = keepaliveFetch();
    const off = renderHook(() => useKeepalive(false));
    fireEvent.pointerDown(window);
    expect(callsTo(fetchMock, KEEPALIVE)).toHaveLength(0);
    off.unmount();
    const on = renderHook(() => useKeepalive(true));
    on.unmount();
    fireEvent.pointerDown(window);
    expect(callsTo(fetchMock, KEEPALIVE)).toHaveLength(0);
  });

  it("ignores a failing keepalive (401s are handled globally)", async () => {
    const fetchMock = mockFetch(() => json({ detail: "x", code: "invalid_request" }, 400));
    renderHook(() => useKeepalive(true));
    fireEvent.pointerDown(window);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(callsTo(fetchMock, KEEPALIVE)).toHaveLength(1);
  });
});

describe("keepalive is mounted by AuthGate only in `authenticated`", () => {
  async function mountGate(gateStatus: unknown) {
    const fetchMock = mockFetch((url) => {
      if (url === "/api/auth/status") return json(gateStatus);
      if (url === KEEPALIVE) return json(KEEPALIVE_BODY);
      return json([]);
    });
    render(createElement(AuthGate, null, createElement("div", { "data-testid": "app" })));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    fireEvent.pointerDown(window);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    return callsTo(fetchMock, KEEPALIVE).length;
  }

  it("authenticated: activity posts a keepalive", async () => {
    expect(await mountGate(AUTHENTICATED_STATUS)).toBe(1);
  });

  it.each([
    ["anonymous", ANONYMOUS_STATUS],
    ["second_factor_required", status({ state: "second_factor_required", pending_username: "alice" })],
    ["enrollment_required", status({ state: "enrollment_required", pending_username: "alice" })],
  ])("%s: activity posts nothing", async (_name, gateStatus) => {
    expect(await mountGate(gateStatus)).toBe(0);
  });
});
