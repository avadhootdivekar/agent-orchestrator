import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, authApi } from "../api";
import { PROOF_STORAGE_KEY } from "../auth/constants";
import { clearProof, proofStorageBlocked, readProof, writeProof } from "../auth/proof";
import { SESSION_PROOF_HEADER } from "../types";
import {
  ANONYMOUS_STATUS,
  USER,
  blockLocalStorage,
  headerOf,
  json,
  mockFetch,
  resetAuthState,
  step,
} from "./fixtures/auth";

beforeEach(() => resetAuthState());
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("proof storage", () => {
  it("round-trips through localStorage under the documented key", () => {
    expect(PROOF_STORAGE_KEY).toBe("ao-session-proof");
    expect(readProof()).toBeNull();
    writeProof("P1");
    expect(localStorage.getItem(PROOF_STORAGE_KEY)).toBe("P1");
    expect(readProof()).toBe("P1");
    clearProof();
    expect(localStorage.getItem(PROOF_STORAGE_KEY)).toBeNull();
    expect(readProof()).toBeNull();
    expect(proofStorageBlocked()).toBe(false);
  });

  it("reads storage on every call, so a rotation written by another tab is seen (R15)", () => {
    writeProof("P1");
    localStorage.setItem(PROOF_STORAGE_KEY, "P2-from-another-tab");
    expect(readProof()).toBe("P2-from-another-tab");
  });

  it("falls back to memory and flags blocked storage when localStorage throws", () => {
    blockLocalStorage();
    expect(readProof()).toBeNull();
    expect(proofStorageBlocked()).toBe(true);
    writeProof("P1");
    expect(readProof()).toBe("P1");
    clearProof();
    expect(readProof()).toBeNull();
  });
});

describe("proof on the wire", () => {
  it("login stores the proof and EVERY later request carries it, including existing api.* calls", async () => {
    const fetchMock = mockFetch((url) => {
      if (url === "/api/auth/login") return json(step({ session_proof: "P1" }));
      if (url === "/api/auth/status") return json(ANONYMOUS_STATUS);
      return json([]);
    });
    await authApi.login("alice", "pw");
    await authApi.status();
    await api.runs();
    await api.workflows();
    const calls = fetchMock.mock.calls;
    // the login request itself predates the proof
    expect(headerOf(calls[0][1], SESSION_PROOF_HEADER)).toBeUndefined();
    for (const [, init] of calls.slice(1)) {
      expect(headerOf(init, SESSION_PROOF_HEADER)).toBe("P1");
      expect(headerOf(init, "Content-Type")).toBe("application/json");
    }
  });

  it("a later issuing response (verify) replaces the proof", async () => {
    const fetchMock = mockFetch((url) =>
      url === "/api/auth/totp/verify" ? json(step({ session_proof: "P2", user: USER })) : json([]),
    );
    writeProof("P1");
    await authApi.verifyTotp("123456");
    await api.runs();
    expect(headerOf(fetchMock.mock.calls[0][1], SESSION_PROOF_HEADER)).toBe("P1");
    expect(headerOf(fetchMock.mock.calls[1][1], SESSION_PROOF_HEADER)).toBe("P2");
    expect(readProof()).toBe("P2");
  });

  it("only a string session_proof rotates the proof", async () => {
    mockFetch(() => json({ session_proof: 42, other: [] }));
    writeProof("P1");
    await api.runs();
    expect(readProof()).toBe("P1");
  });

  it.each([
    ["logout()", () => authApi.logout()],
    ["logout(true)", () => authApi.logout(true)],
  ])("%s removes the proof", async (_name, call) => {
    mockFetch(() => json({ state: "anonymous" }));
    writeProof("P1");
    await call();
    expect(readProof()).toBeNull();
    expect(localStorage.getItem(PROOF_STORAGE_KEY)).toBeNull();
  });

  it("logout sends the proof (so the server can match the session) and the right body", async () => {
    const fetchMock = mockFetch(() => json({ state: "anonymous" }));
    writeProof("P1");
    await authApi.logout(true);
    await authApi.logout();
    const [first, second] = fetchMock.mock.calls;
    expect(headerOf(first[1], SESSION_PROOF_HEADER)).toBe("P1");
    expect(JSON.parse(String(first[1]?.body))).toEqual({ everywhere: true });
    expect(JSON.parse(String(second[1]?.body))).toEqual({});
  });

  it("logout drops the local proof even when the request fails", async () => {
    mockFetch(() => {
      throw new TypeError("Failed to fetch");
    });
    writeProof("P1");
    await expect(authApi.logout()).rejects.toThrow("Failed to fetch");
    expect(readProof()).toBeNull();
  });

  it("with localStorage throwing, the in-memory proof still rides on requests", async () => {
    blockLocalStorage();
    const fetchMock = mockFetch((url) =>
      url === "/api/auth/login" ? json(step({ session_proof: "PM" })) : json([]),
    );
    await authApi.login("alice", "pw");
    await api.runs();
    expect(headerOf(fetchMock.mock.calls[1][1], SESSION_PROOF_HEADER)).toBe("PM");
    expect(proofStorageBlocked()).toBe(true);
  });

  it("never puts the proof in a URL", async () => {
    const fetchMock = mockFetch((url) => (url.startsWith("/api/auth/login") ? json(step({ session_proof: "SECRET-PROOF" })) : json([])));
    await authApi.login("alice", "pw");
    await authApi.status();
    await api.runs();
    await api.listDir("a/b", "root");
    await api.usage(["r1"], true);
    for (const [url] of fetchMock.mock.calls) expect(String(url)).not.toContain("SECRET-PROOF");
  });
});
