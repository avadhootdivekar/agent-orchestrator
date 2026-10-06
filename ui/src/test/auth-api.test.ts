import { act, cleanup, render, screen } from "@testing-library/react";
import { createElement, useCallback } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, authApi, setSessionLossHandler } from "../api";
import { AuthGate } from "../auth/AuthGate";
import { POLL_MS, usePolling } from "../usePolling";
import { SESSION_LOSS_CODES } from "../types";
import {
  ANONYMOUS_STATUS,
  AUTHENTICATED_STATUS,
  callsTo,
  json,
  mockFetch,
  resetAuthState,
} from "./fixtures/auth";

const FIXED_NOW = new Date("2026-10-05T12:00:00Z");
const CONCURRENT_REQUESTS = 10;
const STATUS_PATH = "/api/auth/status";

beforeEach(() => {
  resetAuthState();
  vi.useFakeTimers();
  vi.setSystemTime(FIXED_NOW);
});
afterEach(() => {
  cleanup();
  setSessionLossHandler(null);
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

/** Let promise chains and effects settle without moving the clock. */
async function flush() {
  for (let i = 0; i < 5; i += 1) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
  }
}

describe("ApiError parsing", () => {
  it("stays source-compatible with the two-argument constructor", () => {
    const error = new ApiError("x", 404);
    expect(error.status).toBe(404);
    expect(error.code).toBeUndefined();
    expect(error.retryAfterSeconds).toBeUndefined();
    expect(error.extra).toBeUndefined();
    expect(error.name).toBe("ApiError");
  });

  it("parses code, Retry-After and the body extras", async () => {
    mockFetch(() =>
      json(
        { detail: "Too many attempts.", code: "too_many_attempts", retry_after_seconds: 99 },
        429,
        { "Retry-After": "7" },
      ),
    );
    const error = (await authApi.login("a", "b").catch((e: unknown) => e)) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(429);
    expect(error.message).toBe("Too many attempts.");
    expect(error.code).toBe("too_many_attempts");
    expect(error.retryAfterSeconds).toBe(7); // the header wins over the body key
    expect(error.extra).toMatchObject({ code: "too_many_attempts", retry_after_seconds: 99 });
  });

  it("falls back to the body's retry_after_seconds and keeps per-code keys", async () => {
    mockFetch(() =>
      json({ detail: "bad", code: "invalid_code", reason: "replayed", attempts_remaining: 2, retry_after_seconds: 1 }, 401),
    );
    const error = (await authApi.verifyTotp("123456").catch((e: unknown) => e)) as ApiError;
    expect(error.retryAfterSeconds).toBe(1);
    expect(error.extra).toMatchObject({ reason: "replayed", attempts_remaining: 2 });
  });

  it("ignores a non-positive or garbage Retry-After", async () => {
    mockFetch(() => json({ detail: "x", code: "busy" }, 503, { "Retry-After": "soon" }));
    const error = (await authApi.status().catch((e: unknown) => e)) as ApiError;
    expect(error.retryAfterSeconds).toBeUndefined();
  });

  it("keeps the status text for a non-JSON error body (no code, no extra)", async () => {
    mockFetch(() => new Response("<html>bad gateway</html>", { status: 502, statusText: "Bad Gateway" }));
    const error = (await authApi.status().catch((e: unknown) => e)) as ApiError;
    expect(error.message).toBe("Bad Gateway");
    expect(error.code).toBeUndefined();
    expect(error.extra).toBeUndefined();
  });

  it("still surfaces a plain FastAPI {detail} error from an existing call", async () => {
    mockFetch(() => json({ detail: "Run not found" }, 404));
    await expect(api.run("nope")).rejects.toMatchObject({ status: 404, message: "Run not found" });
  });
});

describe("session-loss handler selection", () => {
  it.each(SESSION_LOSS_CODES.map((code) => [code]))("401 %s calls the handler once with the code", async (code) => {
    const handler = vi.fn();
    setSessionLossHandler(handler);
    mockFetch(() => json({ detail: "gone", code }, 401));
    await expect(api.runs()).rejects.toBeInstanceOf(ApiError);
    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler).toHaveBeenCalledWith(code);
  });

  it.each([
    ["401 invalid_credentials", 401, "invalid_credentials"],
    ["401 invalid_code", 401, "invalid_code"],
    ["401 without a code", 401, undefined],
    ["403 origin_mismatch", 403, "origin_mismatch"],
    ["403 totp_required", 403, "totp_required"],
    // A session-loss code on a non-401 status must not trigger the handler either.
    ["403 with a session-loss code", 403, "not_authenticated"],
    ["429 too_many_attempts", 429, "too_many_attempts"],
    ["503 busy", 503, "busy"],
  ])("%s does not call the handler", async (_name, statusCode, code) => {
    const handler = vi.fn();
    setSessionLossHandler(handler);
    mockFetch(() => json({ detail: "nope", ...(code ? { code } : {}) }, statusCode));
    await expect(authApi.login("a", "b")).rejects.toBeInstanceOf(ApiError);
    expect(handler).not.toHaveBeenCalled();
  });

  it("a successful response does not call the handler", async () => {
    const handler = vi.fn();
    setSessionLossHandler(handler);
    mockFetch(() => json([]));
    await api.runs();
    expect(handler).not.toHaveBeenCalled();
  });

  it("a cleared handler (null) is tolerated", async () => {
    setSessionLossHandler(null);
    mockFetch(() => json({ detail: "gone", code: "not_authenticated" }, 401));
    await expect(api.runs()).rejects.toBeInstanceOf(ApiError);
  });
});

describe("single-flight session loss (AuthGate + usePolling)", () => {
  /** A dashboard stand-in: polls, and its first tick fires N concurrent calls. */
  function Dashboard() {
    const tick = useCallback(async () => {
      await Promise.allSettled(
        Array.from({ length: runsTicks === 0 ? CONCURRENT_REQUESTS : 1 }, () => api.runs()),
      );
      runsTicks += 1;
    }, []);
    usePolling(tick, POLL_MS);
    return createElement("div", { "data-testid": "app" }, "dashboard");
  }
  let runsTicks = 0;

  beforeEach(() => {
    runsTicks = 0;
  });

  it("10 concurrent 401s cause exactly ONE status fetch, then the dashboard stays unmounted and silent", async () => {
    let signedIn = true;
    const fetchMock = mockFetch((url) => {
      if (url === STATUS_PATH) return json(signedIn ? AUTHENTICATED_STATUS : ANONYMOUS_STATUS);
      if (url === "/api/runs") {
        signedIn = false; // the session dies; every concurrent call sees a 401
        return json({ detail: "Not authenticated", code: "not_authenticated" }, 401);
      }
      return json([]);
    });

    render(createElement(AuthGate, null, createElement(Dashboard)));
    await flush();

    expect(callsTo(fetchMock, "/api/runs")).toHaveLength(CONCURRENT_REQUESTS);
    // 1 initial status + exactly 1 for the whole burst of 401s
    expect(callsTo(fetchMock, STATUS_PATH)).toHaveLength(2);
    expect(screen.queryByTestId("app")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();

    const before = fetchMock.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10 * POLL_MS);
    });
    expect(fetchMock.mock.calls.length).toBe(before); // no polling loop survives the unmount
  });
});
