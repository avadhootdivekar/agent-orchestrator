/**
 * Shared client contract (HLD §17.6, §17.10; design-review minor 7; AC-29).
 *
 * The SPA client (`api.ts` + `auth/proof.ts` + `AuthGate`) and the hub page script
 * (`hub/hubAuthCore.ts`) implement the same client protocol twice on purpose: the hub page has no
 * dependencies. This file runs ONE table of scenarios against BOTH clients with a mocked `fetch`,
 * so the two cannot drift silently. Each client sits behind a small driver (`Client`); the
 * scenarios never touch client internals.
 */
import { act, cleanup, render } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, authApi, setSessionLossHandler } from "../api";
import { AuthGate } from "../auth/AuthGate";
import * as spaConstants from "../auth/constants";
import * as hub from "../hub/hubAuthCore";
import { SESSION_LOSS_CODES, SESSION_PROOF_HEADER, type AuthErrorCode } from "../types";
import { ANONYMOUS_STATUS, blockLocalStorage, resetAuthState } from "./fixtures/auth";
import {
  byId,
  click,
  fakeResponse,
  makeWin,
  mountLogin,
  settle,
  submit,
  typeInto,
} from "./fixtures/hub";

// --- The wire: a recording `fetch` -------------------------------------------------------------
type Reply = Response | (() => Response);
type Op = "status" | "login" | "verify" | "confirm" | "other" | "logout";

interface Call {
  url: string;
  init: RequestInit | undefined;
}

class Wire {
  calls: Call[] = [];
  // Status defaults to a well-formed `anonymous` body: both clients parse it.
  private replies = new Map<string, Reply>([["/api/auth/status", fakeResponse(ANONYMOUS_STATUS)]]);
  /** Test-only hook: rewrite what a (deliberately broken) client "sent" before it is recorded. */
  decorate: ((init: RequestInit | undefined) => RequestInit | undefined) | null = null;

  constructor() {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        const sent = this.decorate ? this.decorate(init) : init;
        this.calls.push({ url: String(url), init: sent });
        const reply = this.replies.get(String(url));
        if (reply === undefined) return fakeResponse({});
        return typeof reply === "function" ? reply() : reply;
      }),
    );
  }

  on(path: string, reply: Reply): void {
    this.replies.set(path, reply);
  }

  mark(): number {
    return this.calls.length;
  }

  since(mark: number, path: string): Call[] {
    return this.calls.slice(mark).filter((c) => c.url === path);
  }
}

const headerOf = (call: Call, name: string): string | undefined =>
  (call.init?.headers as Record<string, string> | undefined)?.[name];

// --- Client drivers ----------------------------------------------------------------------------
interface Client {
  readonly name: string;
  readonly proofHeader: string;
  readonly storageKey: string;
  readonly sessionLossCodes: readonly string[];
  /** The path each logical operation requests. */
  readonly paths: Record<Op, string>;
  /** Fresh client instance on a clean slate (no stored proof). */
  start(wire: Wire): Promise<void>;
  /** Perform one operation. Never rejects: failures are the scenarios' business, not the driver's. */
  run(op: Op): Promise<void>;
  /** Does this client treat a 401 with `code` (on the second-factor call) as "your session is gone"? */
  treatsAsSessionLoss(code: string, wire: Wire): Promise<boolean>;
}

const PATHS = {
  status: "/api/auth/status",
  login: "/api/auth/login",
  verify: "/api/auth/totp/verify",
  logout: "/api/auth/logout",
} as const;

const spaClient: Client = {
  name: "SPA (api.ts + proof.ts + AuthGate)",
  proofHeader: SESSION_PROOF_HEADER,
  storageKey: spaConstants.PROOF_STORAGE_KEY,
  sessionLossCodes: SESSION_LOSS_CODES,
  paths: { ...PATHS, confirm: "/api/auth/totp/enroll/confirm", other: "/api/general-instructions" },
  async start() {
    resetAuthState();
    setSessionLossHandler(null);
  },
  async run(op) {
    const swallow = async (p: Promise<unknown>): Promise<void> => {
      await p.then(undefined, () => undefined);
    };
    switch (op) {
      case "status":
        // The SPA clears the proof on `anonymous` in AuthGate, so the status op renders the gate.
        await act(async () => {
          render(createElement(AuthGate, null, createElement("div")));
          await settle();
        });
        cleanup();
        return;
      case "login":
        return swallow(authApi.login("alice", "pw"));
      case "verify":
        return swallow(authApi.verifyTotp("123456"));
      case "confirm":
        return swallow(authApi.enrollConfirm({ code: "123456" }));
      case "other":
        return swallow(api.generalInstructions());
      case "logout":
        return swallow(authApi.logout());
    }
  },
  async treatsAsSessionLoss(code, wire) {
    const handler = vi.fn();
    setSessionLossHandler(handler);
    wire.on(PATHS.verify, fakeResponse({ detail: "x", code }, 401));
    await this.run("verify");
    setSessionLossHandler(null);
    return handler.mock.calls.length > 0;
  },
};

class HubClient implements Client {
  readonly name = "hub page (hubAuthCore.ts)";
  readonly proofHeader = hub.SESSION_PROOF_HEADER;
  readonly storageKey = hub.PROOF_STORAGE_KEY;
  readonly sessionLossCodes = hub.SESSION_LOSS_CODES;
  readonly paths: Record<Op, string> = {
    ...PATHS,
    confirm: "/api/auth/totp/enroll/confirm",
    other: "/api/auth/totp/enroll/begin",
  };
  private doc!: Document;

  /** Mount the page and init the script; the in-memory proof lives as long as this instance. */
  private async mount(): Promise<void> {
    this.doc = mountLogin();
    hub.initHubAuth(this.doc, makeWin().win);
    await settle();
  }

  async start(): Promise<void> {
    resetAuthState();
    await this.mount();
  }

  async run(op: Op): Promise<void> {
    const doc = this.doc;
    switch (op) {
      case "status":
        // The hub script asks for status once per page load: a new load is a new instance.
        await this.mount();
        return;
      case "login":
        typeInto(doc, "ao-username", "alice");
        typeInto(doc, "ao-password", "pw");
        submit(doc, "ao-login-form");
        break;
      case "verify":
        typeInto(doc, "ao-code", "123456");
        submit(doc, "ao-totp-form");
        break;
      case "confirm":
        typeInto(doc, "ao-enroll-code", "123456");
        submit(doc, "ao-enroll-form");
        break;
      case "other":
        typeInto(doc, "ao-enroll-token", "TOKEN");
        submit(doc, "ao-enroll-token-form");
        break;
      case "logout":
        click(doc, "ao-signout");
        break;
    }
    await settle();
  }

  async treatsAsSessionLoss(code: string): Promise<boolean> {
    // The hub's classifier is a pure function (its page reactions are covered in hub-auth.test.ts).
    return hub.isSessionLossCode(code);
  }
}

// --- Scenarios ---------------------------------------------------------------------------------
const ISSUING_OPS: readonly Op[] = ["login", "verify", "confirm"];
const PROOF_SENDING_OPS: readonly Op[] = ["status", "verify", "confirm", "other", "logout"];

/** A body every client accepts for every operation, carrying a rotated proof. */
const issued = (proof: string) =>
  fakeResponse({
    state: "second_factor_required",
    second_factors: ["totp", "recovery_code"],
    recovery_codes: [],
    session_proof: proof,
  });

const ALL_ERROR_CODES: readonly AuthErrorCode[] = [
  "invalid_request", "password_policy", "not_authenticated", "second_factor_required",
  "enrollment_required", "invalid_credentials", "invalid_code", "origin_required",
  "origin_mismatch", "cross_site_request", "insecure_transport", "totp_disabled_by_policy",
  "totp_required", "forbidden", "already_authenticated", "totp_already_enrolled",
  "totp_not_enrolled", "no_pending_enrollment", "body_too_large", "too_many_attempts",
  "busy", "store_unavailable",
];

type Scenario = (client: Client, wire: Wire) => Promise<void>;
const stored = (client: Client): string | null => localStorage.getItem(client.storageKey);

/** Establish proof "P1" through a real login round trip. */
async function loginWithP1(client: Client, wire: Wire): Promise<void> {
  wire.on(client.paths.login, issued("P1"));
  await client.run("login");
  expect(stored(client)).toBe("P1");
}

const SCENARIOS: Array<[string, Scenario]> = [
  [
    "the proof header name and the storage key are the contract values",
    async (client, wire) => {
      expect(client.proofHeader).toBe("X-AO-Session-Proof");
      expect(client.storageKey).toBe("ao-session-proof");
      await loginWithP1(client, wire);
      const mark = wire.mark();
      await client.run("other");
      const [call] = wire.since(mark, client.paths.other);
      expect(Object.keys(call.init?.headers ?? {})).toContain("X-AO-Session-Proof");
    },
  ],
  [
    "no proof header is sent before a proof exists",
    async (client, wire) => {
      const mark = wire.mark();
      await client.run("other");
      const [call] = wire.since(mark, client.paths.other);
      expect(headerOf(call, client.proofHeader)).toBeUndefined();
    },
  ],
  ...ISSUING_OPS.map((op): [string, Scenario] => [
    `session_proof from the ${op} response replaces the stored proof`,
    async (client, wire) => {
      localStorage.setItem(client.storageKey, "OLD");
      wire.on(client.paths[op], issued(`NEW-${op}`));
      await client.run(op);
      expect(stored(client)).toBe(`NEW-${op}`);
    },
  ]),
  ...PROOF_SENDING_OPS.map((op): [string, Scenario] => [
    `the stored proof is sent on ${op}`,
    async (client, wire) => {
      await loginWithP1(client, wire);
      const mark = wire.mark();
      await client.run(op);
      const sent = wire.since(mark, client.paths[op]);
      expect(sent.length).toBeGreaterThan(0);
      for (const call of sent) expect(headerOf(call, client.proofHeader)).toBe("P1");
    },
  ]),
  [
    "the proof is cleared on logout",
    async (client, wire) => {
      await loginWithP1(client, wire);
      await client.run("logout");
      expect(stored(client)).toBeNull();
    },
  ],
  [
    "the proof is cleared on logout even when the request fails",
    async (client, wire) => {
      await loginWithP1(client, wire);
      wire.on(client.paths.logout, fakeResponse({ detail: "down", code: "store_unavailable" }, 503));
      await client.run("logout");
      expect(stored(client)).toBeNull();
    },
  ],
  [
    "the proof is cleared when status reports anonymous",
    async (client, wire) => {
      await loginWithP1(client, wire);
      wire.on(client.paths.status, fakeResponse(ANONYMOUS_STATUS));
      await client.run("status");
      expect(stored(client)).toBeNull();
    },
  ],
  [
    "the session-loss codes are exactly SESSION_LOSS_CODES",
    async (client, wire) => {
      expect([...client.sessionLossCodes].sort()).toEqual(
        ["enrollment_required", "not_authenticated", "second_factor_required"],
      );
      expect([...client.sessionLossCodes].sort()).toEqual([...SESSION_LOSS_CODES].sort());
      for (const code of ALL_ERROR_CODES) {
        const expected = (SESSION_LOSS_CODES as readonly string[]).includes(code);
        expect(await client.treatsAsSessionLoss(code, wire), code).toBe(expected);
      }
    },
  ],
  [
    "no fetch call passes a `mode` option",
    async (client, wire) => {
      await loginWithP1(client, wire);
      for (const op of ["verify", "confirm", "other", "status", "logout"] as const) await client.run(op);
      expect(wire.calls.length).toBeGreaterThan(5);
      for (const call of wire.calls) expect(Object.keys(call.init ?? {}), call.url).not.toContain("mode");
    },
  ],
  [
    "the in-memory fallback carries the proof when localStorage throws",
    async (client, wire) => {
      blockLocalStorage();
      wire.on(client.paths.login, issued("P1"));
      await client.run("login");
      const mark = wire.mark();
      await client.run("verify");
      const [call] = wire.since(mark, client.paths.verify);
      expect(headerOf(call, client.proofHeader)).toBe("P1");
    },
  ],
];

const CLIENTS: readonly Client[] = [spaClient, new HubClient()];

let wire: Wire;
beforeEach(() => {
  wire = new Wire();
});
afterEach(() => {
  cleanup();
  setSessionLossHandler(null);
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe.each(CLIENTS)("shared client contract: $name", (client) => {
  it.each(SCENARIOS)("%s", async (_name, scenario) => {
    await client.start(wire);
    await scenario(client, wire);
  });
});

describe("the two clients duplicate the same constants and copy", () => {
  it("proof header, storage key, loss codes, keepalive interval and countdown tick are equal", () => {
    expect(hub.SESSION_PROOF_HEADER).toBe(SESSION_PROOF_HEADER);
    expect(hub.PROOF_STORAGE_KEY).toBe(spaConstants.PROOF_STORAGE_KEY);
    expect([...hub.SESSION_LOSS_CODES]).toEqual([...SESSION_LOSS_CODES]);
    expect(hub.KEEPALIVE_MIN_INTERVAL_MS).toBe(spaConstants.KEEPALIVE_MIN_INTERVAL_MS);
    expect(hub.COUNTDOWN_TICK_MS).toBe(spaConstants.COUNTDOWN_TICK_MS);
  });

  it("the shared HLD §17.8 strings are equal", () => {
    expect(hub.MSG_INVALID_CREDENTIALS).toBe(spaConstants.MSG_INVALID_CREDENTIALS);
    expect(hub.MSG_SERVER_BUSY).toBe(spaConstants.MSG_SERVER_BUSY);
    expect(hub.MSG_SESSION_TIMED_OUT).toBe(spaConstants.MSG_SESSION_TIMED_OUT);
    expect(hub.MSG_TOTP_REQUIRED_POLICY_OFF).toBe(spaConstants.MSG_TOTP_REQUIRED_POLICY_OFF);
    expect(hub.MSG_REPLAYED_CODE).toBe(spaConstants.MSG_REPLAYED_CODE);
    expect(hub.msgTooManyAttempts(7)).toBe(spaConstants.msgTooManyAttempts(7));
    expect(hub.msgInvalidCode(3)).toBe(spaConstants.msgInvalidCode(3));
    expect(hub.msgInvalidCode(undefined)).toBe(spaConstants.msgInvalidCode(undefined));
  });
});

describe("negative controls: a drifted client makes the matching row fail", () => {
  const scenario = (name: string): Scenario => {
    const found = SCENARIOS.find(([n]) => n === name);
    if (!found) throw new Error(`no scenario "${name}"`);
    return found[1];
  };

  it.each(CLIENTS)("$name: a client that keeps its proof on `anonymous` fails the clear-on-anonymous row", async (client) => {
    const broken: Client = {
      ...client,
      start: (w) => client.start(w),
      run: async (op) => {
        const before = op === "status" ? localStorage.getItem(client.storageKey) : null;
        await client.run(op);
        if (before !== null) localStorage.setItem(client.storageKey, before);
      },
      treatsAsSessionLoss: (code, w) => client.treatsAsSessionLoss(code, w),
    };
    await client.start(wire);
    await expect(
      scenario("the proof is cleared when status reports anonymous")(broken, wire),
    ).rejects.toThrow();
  });

  it.each(CLIENTS)("$name: a client that drops the proof header fails the sent-on-every-call row", async (client) => {
    await client.start(wire);
    wire.decorate = (init) =>
      init === undefined
        ? init
        : { ...init, headers: Object.fromEntries(Object.entries(init.headers ?? {}).filter(([k]) => k !== client.proofHeader)) };
    await expect(scenario("the stored proof is sent on other")(client, wire)).rejects.toThrow();
  });

  it.each(CLIENTS)("$name: a client that adds a `mode` option fails the no-mode row", async (client) => {
    await client.start(wire);
    wire.decorate = (init) => ({ ...init, mode: "cors" });
    await expect(scenario("no fetch call passes a `mode` option")(client, wire)).rejects.toThrow();
  });

  it.each(CLIENTS)("$name: a client with a different loss-code set fails the loss-code row", async (client) => {
    const broken: Client = {
      ...client,
      treatsAsSessionLoss: async (code, w) =>
        code === "enrollment_required" ? false : client.treatsAsSessionLoss(code, w),
    };
    await client.start(wire);
    await expect(
      scenario("the session-loss codes are exactly SESSION_LOSS_CODES")(broken, wire),
    ).rejects.toThrow();
  });

  it("the hub driver reaches the DOM it claims to drive (sanity: ids exist in the fixture)", () => {
    const doc = mountLogin();
    for (const id of ["ao-signout", "ao-login-form", "ao-totp-form", "ao-enroll-form", "ao-enroll-token-form"]) {
      expect(byId(doc, id)).toBeTruthy();
    }
  });
});
