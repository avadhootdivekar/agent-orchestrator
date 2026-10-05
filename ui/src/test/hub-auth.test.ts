/// <reference types="vite/client" />
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  KEEPALIVE_MIN_INTERVAL_MS,
  MSG_INSECURE_TRANSPORT,
  MSG_INVALID_CREDENTIALS,
  MSG_REPLAYED_CODE,
  MSG_SERVER_BUSY,
  MSG_SESSION_TIMED_OUT,
  MSG_TOTP_REQUIRED_POLICY_OFF,
  groupSecret,
  initHubAuth,
  msgInvalidCode,
  msgTooManyAttempts,
} from "../hub/hubAuthCore";
import {
  ANONYMOUS_STATUS,
  type Handler,
  blockLocalStorage,
  bodyOf,
  callsTo,
  headerOf,
  mockFetch,
  resetAuthState,
  status,
} from "./fixtures/auth";
import {
  byId,
  click,
  fakeResponse,
  isShown,
  makeWin,
  mountIndex,
  mountLogin,
  setVisibility,
  settle,
  submit,
  typeInto,
  visibleStep,
} from "./fixtures/hub";

const PROOF_KEY = "ao-session-proof";
const PROOF_HEADER = "X-AO-Session-Proof";
const INJECTION = "<img src=x onerror=alert(1)>";
const SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP";
const URI = "otpauth://totp/ao%40devbox:alice?secret=JBSWY3DPEHPK3PXP&issuer=ao%40devbox";
const RECOVERY_CODES = Array.from({ length: 10 }, (_, i) => `CODE-${i}-ABCD-EFGH`);

type Reply = ReturnType<typeof fakeResponse>;
/** Per-path replies for the mocked fetch; a path with no entry answers 200 `{}`. */
type Routes = Record<string, Reply | (() => Reply)>;

function router(routes: Routes): Handler {
  return (url) => {
    const entry = routes[url];
    return entry === undefined ? fakeResponse({}) : typeof entry === "function" ? entry() : entry;
  };
}

async function bootLogin(routes: Routes = {}, options: { clipboard?: boolean } = {}) {
  const fetchMock = mockFetch(router({ "/api/auth/status": fakeResponse(ANONYMOUS_STATUS), ...routes }));
  const doc = mountLogin();
  const fake = makeWin(options);
  initHubAuth(doc, fake.win);
  await settle();
  return { doc, fetchMock, ...fake };
}

async function doSubmit(doc: Document, formId: string): Promise<Event> {
  const event = submit(doc, formId);
  await settle();
  return event;
}

async function signIn(doc: Document): Promise<void> {
  typeInto(doc, "ao-username", "alice");
  typeInto(doc, "ao-password", "pw");
  await doSubmit(doc, "ao-login-form");
}

beforeEach(() => resetAuthState());
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("login page: initial status", () => {
  it("anonymous: shows only the login form, GETs status without a proof, no banner", async () => {
    const { doc, fetchMock, replace } = await bootLogin();
    expect(visibleStep(doc)).toBe("login");
    expect(isShown(doc, "ao-transport-warning")).toBe(false);
    expect(isShown(doc, "ao-error")).toBe(false);
    const [[url, init]] = fetchMock.mock.calls;
    expect(url).toBe("/api/auth/status");
    expect(init?.method).toBe("GET");
    expect(init?.body).toBeUndefined();
    expect(headerOf(init, PROOF_HEADER)).toBeUndefined();
    expect(replace).not.toHaveBeenCalled();
  });

  it("authenticated: location.replace('/') exactly once", async () => {
    const { replace } = await bootLogin({
      "/api/auth/status": fakeResponse(status({ state: "authenticated" })),
    });
    expect(replace).toHaveBeenCalledTimes(1);
    expect(replace).toHaveBeenCalledWith("/");
  });

  it("second_factor_required: shows the TOTP form; the stored proof is sent on the status call", async () => {
    localStorage.setItem(PROOF_KEY, "P0");
    const { doc, fetchMock } = await bootLogin({
      "/api/auth/status": fakeResponse(status({ state: "second_factor_required" })),
    });
    expect(visibleStep(doc)).toBe("totp");
    expect(headerOf(fetchMock.mock.calls[0][1], PROOF_HEADER)).toBe("P0");
  });

  it("enrollment_required: shows the enrollment-token form first, without the secret rows", async () => {
    const { doc } = await bootLogin({
      "/api/auth/status": fakeResponse(status({ state: "enrollment_required" })),
    });
    expect(visibleStep(doc)).toBe("enroll_token");
    expect(byId(doc, "ao-enroll-secret").parentElement?.hidden).toBe(true);
    expect(byId(doc, "ao-enroll-uri").parentElement?.hidden).toBe(true);
  });

  it("anonymous status clears a stale stored proof", async () => {
    localStorage.setItem(PROOF_KEY, "STALE");
    await bootLogin();
    expect(localStorage.getItem(PROOF_KEY)).toBeNull();
  });

  it("a failed status call falls back to the login form with the server's detail", async () => {
    const { doc } = await bootLogin({
      "/api/auth/status": fakeResponse({ detail: "Boom", code: "x" }, 500),
    });
    expect(visibleStep(doc)).toBe("login");
    expect(byId(doc, "ao-error").textContent).toBe("Boom");
  });

  it("an unreachable server shows the network message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("network down");
      }),
    );
    const doc = mountLogin();
    initHubAuth(doc, makeWin().win);
    await settle();
    expect(visibleStep(doc)).toBe("login");
    expect(byId(doc, "ao-error").textContent).toBe("Could not reach the server. Please try again.");
  });
});

describe("transport banner (AC 12, v2.1 proxy_suspected)", () => {
  const cases = [
    ["plain HTTP from a non-loopback client (proxy suspected)", { secure: false, client_is_loopback: false, proxy_suspected: true }, true],
    ["plain HTTP from a non-loopback client", { secure: false, client_is_loopback: false, proxy_suspected: false }, true],
    ["plain HTTP from loopback", { secure: false, client_is_loopback: true, proxy_suspected: false }, false],
    ["HTTPS", { secure: true, client_is_loopback: false, proxy_suspected: false }, false],
  ] as const;

  it.each(cases)("%s", async (_name, transport, shown) => {
    const { doc } = await bootLogin({ "/api/auth/status": fakeResponse(status({ transport })) });
    expect(isShown(doc, "ao-transport-warning")).toBe(shown);
  });

  it("a null transport shows no banner", async () => {
    const { doc } = await bootLogin({ "/api/auth/status": fakeResponse(status({ transport: null })) });
    expect(isShown(doc, "ao-transport-warning")).toBe(false);
  });
});

describe("password step (AC 1)", () => {
  it("POSTs JSON {username,password} with no `mode`; second_factor_required -> TOTP form + stored proof", async () => {
    const { doc, fetchMock, replace } = await bootLogin({
      "/api/auth/login": fakeResponse({
        state: "second_factor_required",
        second_factors: ["totp", "recovery_code"],
        session_proof: "P1",
      }),
    });
    await signIn(doc);
    const [login] = callsTo(fetchMock, "/api/auth/login");
    expect(login[1]?.method).toBe("POST");
    expect(bodyOf(login[1])).toEqual({ username: "alice", password: "pw" });
    expect(headerOf(login[1], "Content-Type")).toBe("application/json");
    expect(Object.keys(login[1] ?? {})).not.toContain("mode");
    expect(visibleStep(doc)).toBe("totp");
    expect(isShown(doc, "ao-login-form")).toBe(false);
    expect(localStorage.getItem(PROOF_KEY)).toBe("P1");
    expect(replace).not.toHaveBeenCalled();
    expect(byId<HTMLInputElement>(doc, "ao-password").value).toBe("");
  });

  it("authenticated -> location.replace('/') exactly once", async () => {
    const { doc, replace } = await bootLogin({
      "/api/auth/login": fakeResponse({ state: "authenticated", session_proof: "P1" }),
    });
    await signIn(doc);
    expect(replace).toHaveBeenCalledTimes(1);
    expect(replace).toHaveBeenCalledWith("/");
  });

  it("enrollment_required -> the token form", async () => {
    const { doc } = await bootLogin({
      "/api/auth/login": fakeResponse({
        state: "enrollment_required",
        enrollment_token_required: true,
        session_proof: "P1",
      }),
    });
    await signIn(doc);
    expect(visibleStep(doc)).toBe("enroll_token");
  });

  it("403 totp_required -> the exact policy-off string", async () => {
    const { doc } = await bootLogin({
      "/api/auth/login": fakeResponse({ detail: "ignored", code: "totp_required" }, 403),
    });
    await signIn(doc);
    expect(byId(doc, "ao-error").textContent).toBe(MSG_TOTP_REQUIRED_POLICY_OFF);
    expect(isShown(doc, "ao-error")).toBe(true);
    expect(visibleStep(doc)).toBe("login");
  });

  it("401 invalid_credentials and 503 busy map to their exact strings", async () => {
    let reply = fakeResponse({ detail: "x", code: "invalid_credentials" }, 401);
    const { doc } = await bootLogin({ "/api/auth/login": () => reply });
    await signIn(doc);
    expect(byId(doc, "ao-error").textContent).toBe(MSG_INVALID_CREDENTIALS);
    reply = fakeResponse({ detail: "x", code: "busy", retry_after_seconds: 1 }, 503, { "Retry-After": "1" });
    await signIn(doc);
    expect(byId(doc, "ao-error").textContent).toBe(MSG_SERVER_BUSY);
  });

  it("an unmapped code shows the server's detail", async () => {
    const { doc } = await bootLogin({
      "/api/auth/login": fakeResponse({ detail: "Request body is not an object.", code: "invalid_request" }, 400),
    });
    await signIn(doc);
    expect(byId(doc, "ao-error").textContent).toBe("Request body is not an object.");
  });

  it("every submit handler calls preventDefault", async () => {
    const { doc } = await bootLogin();
    for (const id of ["ao-login-form", "ao-totp-form", "ao-enroll-token-form", "ao-enroll-form"]) {
      expect((await doSubmit(doc, id)).defaultPrevented, id).toBe(true);
    }
  });

  it("429 on login locks the login form with a live countdown", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-05T12:00:00Z"));
    const { doc } = await bootLogin({
      "/api/auth/login": fakeResponse({ detail: "x", code: "too_many_attempts", retry_after_seconds: 2 }, 429, {
        "Retry-After": "2",
      }),
    });
    await signIn(doc);
    const button = doc.querySelector<HTMLButtonElement>("#ao-login-form button[type=submit]");
    expect(button?.disabled).toBe(true);
    expect(byId(doc, "ao-error").textContent).toBe(msgTooManyAttempts(2));
    await vi.advanceTimersByTimeAsync(2000);
    expect(button?.disabled).toBe(false);
    expect(isShown(doc, "ao-error")).toBe(false);
  });
});

describe("session proof (AC 2)", () => {
  it("every later call carries the proof; a rotated proof replaces it; anonymous status removes it", async () => {
    let state = "second_factor_required";
    const { doc, fetchMock } = await bootLogin({
      "/api/auth/login": fakeResponse({ state: "second_factor_required", session_proof: "P1" }),
      "/api/auth/totp/verify": () =>
        fakeResponse({ state: "second_factor_required", session_proof: "P2" }),
      "/api/auth/status": () => fakeResponse(status({ state: state as "anonymous" })),
    });
    state = "anonymous";
    await signIn(doc);
    expect(localStorage.getItem(PROOF_KEY)).toBe("P1");
    typeInto(doc, "ao-code", "123456");
    await doSubmit(doc, "ao-totp-form");
    // The verify call carried P1; its response rotated the stored proof to P2.
    const [verify] = callsTo(fetchMock, "/api/auth/totp/verify");
    expect(headerOf(verify[1], PROOF_HEADER)).toBe("P1");
    expect(localStorage.getItem(PROOF_KEY)).toBe("P2");
    typeInto(doc, "ao-code", "123456");
    await doSubmit(doc, "ao-totp-form");
    expect(headerOf(callsTo(fetchMock, "/api/auth/totp/verify")[1][1], PROOF_HEADER)).toBe("P2");
  });

  it("logout (Sign out on the TOTP step) sends the proof, removes it and resets to the login form", async () => {
    const { doc, fetchMock } = await bootLogin({
      "/api/auth/login": fakeResponse({ state: "second_factor_required", session_proof: "P1" }),
      "/api/auth/logout": fakeResponse({ state: "anonymous" }),
    });
    await signIn(doc);
    click(doc, "ao-signout");
    await settle();
    const [logout] = callsTo(fetchMock, "/api/auth/logout");
    expect(logout[1]?.method).toBe("POST");
    expect(headerOf(logout[1], PROOF_HEADER)).toBe("P1");
    expect(localStorage.getItem(PROOF_KEY)).toBeNull();
    expect(visibleStep(doc)).toBe("login");
  });

  it("with localStorage blocked the flow completes using the in-memory proof", async () => {
    blockLocalStorage();
    const { doc, fetchMock, replace } = await bootLogin({
      "/api/auth/login": fakeResponse({ state: "second_factor_required", session_proof: "P1" }),
      "/api/auth/totp/verify": fakeResponse({ state: "authenticated", session_proof: "P2" }),
    });
    await signIn(doc);
    expect(visibleStep(doc)).toBe("totp");
    typeInto(doc, "ao-code", "123456");
    await doSubmit(doc, "ao-totp-form");
    expect(headerOf(callsTo(fetchMock, "/api/auth/totp/verify")[0][1], PROOF_HEADER)).toBe("P1");
    expect(replace).toHaveBeenCalledWith("/");
  });

  it("a response without session_proof leaves the stored proof alone", async () => {
    localStorage.setItem(PROOF_KEY, "P1");
    const { doc } = await bootLogin({
      "/api/auth/status": fakeResponse(status({ state: "second_factor_required" })),
      "/api/auth/totp/verify": fakeResponse({ detail: "no", code: "invalid_code", reason: "invalid" }, 401),
    });
    typeInto(doc, "ao-code", "000000");
    await doSubmit(doc, "ao-totp-form");
    expect(localStorage.getItem(PROOF_KEY)).toBe("P1");
  });
});

describe("TOTP step (AC 3)", () => {
  const secondFactor = { "/api/auth/status": fakeResponse(status({ state: "second_factor_required" })) };

  it("sends {code}", async () => {
    const { doc, fetchMock, replace } = await bootLogin({
      ...secondFactor,
      "/api/auth/totp/verify": fakeResponse({ state: "authenticated", session_proof: "P2" }),
    });
    typeInto(doc, "ao-code", " 123456 ");
    await doSubmit(doc, "ao-totp-form");
    expect(bodyOf(callsTo(fetchMock, "/api/auth/totp/verify")[0][1])).toEqual({ code: "123456" });
    expect(replace).toHaveBeenCalledWith("/");
  });

  it("replayed -> the exact 'already used' string", async () => {
    const { doc } = await bootLogin({
      ...secondFactor,
      "/api/auth/totp/verify": fakeResponse(
        { detail: "x", code: "invalid_code", reason: "replayed", attempts_remaining: 4 },
        401,
      ),
    });
    typeInto(doc, "ao-code", "123456");
    await doSubmit(doc, "ao-totp-form");
    expect(byId(doc, "ao-error").textContent).toBe(MSG_REPLAYED_CODE);
  });

  it("invalid -> the invalid string including attempts_remaining; the code input is cleared", async () => {
    const { doc } = await bootLogin({
      ...secondFactor,
      "/api/auth/totp/verify": fakeResponse(
        { detail: "x", code: "invalid_code", reason: "invalid", attempts_remaining: 3 },
        401,
      ),
    });
    typeInto(doc, "ao-code", "123456");
    await doSubmit(doc, "ao-totp-form");
    expect(byId(doc, "ao-error").textContent).toBe(msgInvalidCode(3));
    expect(byId(doc, "ao-error").textContent).toContain("(3 attempts left)");
    expect(byId<HTMLInputElement>(doc, "ao-code").value).toBe("");
  });

  it("429 with Retry-After: 3 disables submit and counts 3 -> 0, then re-enables (fake timers)", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-05T12:00:00Z"));
    const { doc } = await bootLogin({
      ...secondFactor,
      "/api/auth/totp/verify": fakeResponse({ detail: "x", code: "too_many_attempts" }, 429, {
        "Retry-After": "3",
      }),
    });
    typeInto(doc, "ao-code", "123456");
    await doSubmit(doc, "ao-totp-form");
    const verifyButton = doc.querySelector<HTMLButtonElement>("#ao-totp-form button[type=submit]");
    expect(verifyButton?.disabled).toBe(true);
    expect(byId(doc, "ao-error").textContent).toBe(msgTooManyAttempts(3));
    await vi.advanceTimersByTimeAsync(1000);
    expect(byId(doc, "ao-error").textContent).toBe(msgTooManyAttempts(2));
    expect(verifyButton?.disabled).toBe(true);
    await vi.advanceTimersByTimeAsync(1000);
    expect(byId(doc, "ao-error").textContent).toBe(msgTooManyAttempts(1));
    await vi.advanceTimersByTimeAsync(1000);
    expect(verifyButton?.disabled).toBe(false);
    expect(isShown(doc, "ao-error")).toBe(false);
  });

  it("falls back to the body's retry_after_seconds when there is no Retry-After header", async () => {
    vi.useFakeTimers();
    const { doc } = await bootLogin({
      ...secondFactor,
      "/api/auth/totp/verify": fakeResponse({ detail: "x", code: "too_many_attempts", retry_after_seconds: 7 }, 429),
    });
    typeInto(doc, "ao-code", "123456");
    await doSubmit(doc, "ao-totp-form");
    expect(byId(doc, "ao-error").textContent).toBe(msgTooManyAttempts(7));
  });

  it("the toggle swaps to the recovery input and sends {recovery_code}", async () => {
    const { doc, fetchMock } = await bootLogin({
      ...secondFactor,
      "/api/auth/totp/verify": fakeResponse({ state: "authenticated", session_proof: "P2" }),
    });
    expect(isShown(doc, "ao-code-label")).toBe(true);
    expect(isShown(doc, "ao-recovery-label")).toBe(false);
    click(doc, "ao-toggle-recovery");
    expect(isShown(doc, "ao-code-label")).toBe(false);
    expect(isShown(doc, "ao-recovery-label")).toBe(true);
    typeInto(doc, "ao-recovery", "ABCD-EFGH-JKMN-PQRS");
    await doSubmit(doc, "ao-totp-form");
    expect(bodyOf(callsTo(fetchMock, "/api/auth/totp/verify")[0][1])).toEqual({
      recovery_code: "ABCD-EFGH-JKMN-PQRS",
    });
    click(doc, "ao-toggle-recovery");
    expect(isShown(doc, "ao-code-label")).toBe(true);
  });

  it("401 not_authenticated -> the timeout notice, the login form, and the proof is dropped", async () => {
    localStorage.setItem(PROOF_KEY, "P1");
    const { doc } = await bootLogin({
      ...secondFactor,
      "/api/auth/totp/verify": fakeResponse({ detail: "x", code: "not_authenticated" }, 401),
    });
    typeInto(doc, "ao-code", "123456");
    await doSubmit(doc, "ao-totp-form");
    expect(visibleStep(doc)).toBe("login");
    expect(byId(doc, "ao-error").textContent).toBe(MSG_SESSION_TIMED_OUT);
    expect(isShown(doc, "ao-error")).toBe(true);
    expect(localStorage.getItem(PROOF_KEY)).toBeNull();
  });

  it("401 enrollment_required on verify moves to the token form", async () => {
    const { doc } = await bootLogin({
      ...secondFactor,
      "/api/auth/totp/verify": fakeResponse({ detail: "x", code: "enrollment_required" }, 401),
    });
    typeInto(doc, "ao-code", "123456");
    await doSubmit(doc, "ao-totp-form");
    expect(visibleStep(doc)).toBe("enroll_token");
  });
});

describe("forced enrollment, text only (AC 4)", () => {
  const enrollmentRequired = { "/api/auth/status": fakeResponse(status({ state: "enrollment_required" })) };
  const beginOk = fakeResponse({
    secret: SECRET,
    otpauth_uri: URI,
    issuer: "ao@devbox",
    account: "alice",
    algorithm: "SHA1",
    digits: 6,
    period: 30,
  });

  it("token form -> begin -> secret grouped in 4s and the URI as text; no svg or canvas", async () => {
    const { doc, fetchMock } = await bootLogin({ ...enrollmentRequired, "/api/auth/totp/enroll/begin": beginOk });
    expect(visibleStep(doc)).toBe("enroll_token");
    typeInto(doc, "ao-enroll-token", "ABCD-EFGH-JKMN-PQRS");
    await doSubmit(doc, "ao-enroll-token-form");
    const [begin] = callsTo(fetchMock, "/api/auth/totp/enroll/begin");
    expect(begin[1]?.method).toBe("POST");
    expect(bodyOf(begin[1])).toEqual({ enrollment_token: "ABCD-EFGH-JKMN-PQRS" });
    expect(visibleStep(doc)).toBe("enroll_confirm");
    expect(byId(doc, "ao-enroll-secret").textContent).toBe(groupSecret(SECRET));
    expect(byId(doc, "ao-enroll-secret").textContent).toBe("JBSW Y3DP EHPK 3PXP JBSW Y3DP EHPK 3PXP");
    expect(byId(doc, "ao-enroll-uri").textContent).toBe(URI);
    expect(doc.querySelector("svg, canvas")).toBeNull();
    expect(byId<HTMLInputElement>(doc, "ao-enroll-token").value).toBe("");
  });

  it("confirm -> {code}, 10 <li> recovery codes, Continue gated by the checkbox", async () => {
    const { doc, fetchMock, replace } = await bootLogin({
      ...enrollmentRequired,
      "/api/auth/totp/enroll/begin": beginOk,
      "/api/auth/totp/enroll/confirm": fakeResponse({
        state: "authenticated",
        recovery_codes: RECOVERY_CODES,
        session_proof: "P9",
      }),
    });
    typeInto(doc, "ao-enroll-token", "T");
    await doSubmit(doc, "ao-enroll-token-form");
    typeInto(doc, "ao-enroll-code", "123456");
    await doSubmit(doc, "ao-enroll-form");
    expect(bodyOf(callsTo(fetchMock, "/api/auth/totp/enroll/confirm")[0][1])).toEqual({ code: "123456" });
    expect(localStorage.getItem(PROOF_KEY)).toBe("P9");
    expect(visibleStep(doc)).toBe("codes");
    const items = Array.from(doc.querySelectorAll("#ao-recovery-list > li"));
    expect(items.map((li) => li.textContent)).toEqual(RECOVERY_CODES);
    // The secret is not left behind in the page.
    expect(byId(doc, "ao-enroll-secret").textContent).toBe("");

    const next = byId<HTMLButtonElement>(doc, "ao-continue");
    expect(next.disabled).toBe(true);
    click(doc, "ao-continue");
    expect(replace).not.toHaveBeenCalled();
    const box = byId<HTMLInputElement>(doc, "ao-codes-ack");
    box.checked = true;
    box.dispatchEvent(new Event("change", { bubbles: true }));
    expect(next.disabled).toBe(false);
    click(doc, "ao-continue");
    expect(replace).toHaveBeenCalledTimes(1);
    expect(replace).toHaveBeenCalledWith("/");
    box.checked = false;
    box.dispatchEvent(new Event("change", { bubbles: true }));
    expect(next.disabled).toBe(true);
  });

  it("confirm: a wrong code shows the invalid string with attempts_remaining and stays on the step", async () => {
    const { doc } = await bootLogin({
      ...enrollmentRequired,
      "/api/auth/totp/enroll/begin": beginOk,
      "/api/auth/totp/enroll/confirm": fakeResponse(
        { detail: "x", code: "invalid_code", reason: "invalid", attempts_remaining: 4 },
        401,
      ),
    });
    typeInto(doc, "ao-enroll-token", "T");
    await doSubmit(doc, "ao-enroll-token-form");
    typeInto(doc, "ao-enroll-code", "000000");
    await doSubmit(doc, "ao-enroll-form");
    expect(visibleStep(doc)).toBe("enroll_confirm");
    expect(byId(doc, "ao-error").textContent).toBe(msgInvalidCode(4));
  });

  it("403 insecure_transport on begin -> the exact string", async () => {
    const { doc } = await bootLogin({
      ...enrollmentRequired,
      "/api/auth/totp/enroll/begin": fakeResponse({ detail: "x", code: "insecure_transport" }, 403),
    });
    typeInto(doc, "ao-enroll-token", "T");
    await doSubmit(doc, "ao-enroll-token-form");
    expect(byId(doc, "ao-error").textContent).toBe(MSG_INSECURE_TRANSPORT);
    expect(visibleStep(doc)).toBe("enroll_token");
  });

  it("401 second_factor_required on begin moves to the TOTP form (session-loss routing)", async () => {
    const { doc } = await bootLogin({
      ...enrollmentRequired,
      "/api/auth/totp/enroll/begin": fakeResponse({ detail: "x", code: "second_factor_required" }, 401),
    });
    typeInto(doc, "ao-enroll-token", "T");
    await doSubmit(doc, "ao-enroll-token-form");
    expect(visibleStep(doc)).toBe("totp");
  });

  it("401 invalid_code on begin shows the server's detail", async () => {
    const { doc } = await bootLogin({
      ...enrollmentRequired,
      "/api/auth/totp/enroll/begin": fakeResponse(
        { detail: "That enrollment token is wrong or expired.", code: "invalid_code", reason: "invalid" },
        401,
      ),
    });
    typeInto(doc, "ao-enroll-token", "T");
    await doSubmit(doc, "ao-enroll-token-form");
    expect(byId(doc, "ao-error").textContent).toBe("That enrollment token is wrong or expired.");
  });

  it("Copy buttons call the guarded clipboard API (raw secret, URI, all codes)", async () => {
    const { doc, writeText } = await bootLogin({
      ...enrollmentRequired,
      "/api/auth/totp/enroll/begin": beginOk,
      "/api/auth/totp/enroll/confirm": fakeResponse({
        state: "authenticated",
        recovery_codes: RECOVERY_CODES,
        session_proof: "P9",
      }),
    });
    typeInto(doc, "ao-enroll-token", "T");
    await doSubmit(doc, "ao-enroll-token-form");
    click(doc, "ao-copy-secret");
    click(doc, "ao-copy-uri");
    await settle();
    expect(writeText).toHaveBeenNthCalledWith(1, SECRET);
    expect(writeText).toHaveBeenNthCalledWith(2, URI);
    expect(byId(doc, "ao-copy-secret").textContent).toBe("Copied");
    typeInto(doc, "ao-enroll-code", "123456");
    await doSubmit(doc, "ao-enroll-form");
    click(doc, "ao-copy-codes");
    await settle();
    expect(writeText).toHaveBeenNthCalledWith(3, RECOVERY_CODES.join("\n"));
  });

  it("the Copy buttons are hidden when the clipboard API is missing, and a rejected copy is reported", async () => {
    const without = await bootLogin(enrollmentRequired, { clipboard: false });
    for (const id of ["ao-copy-secret", "ao-copy-uri", "ao-copy-codes"]) {
      expect(byId(without.doc, id).hidden, id).toBe(true);
    }
    const failing = await bootLogin({ ...enrollmentRequired, "/api/auth/totp/enroll/begin": beginOk });
    failing.writeText.mockRejectedValueOnce(new Error("denied"));
    typeInto(failing.doc, "ao-enroll-token", "T");
    await doSubmit(failing.doc, "ao-enroll-token-form");
    click(failing.doc, "ao-copy-secret");
    await settle();
    expect(byId(failing.doc, "ao-copy-secret").textContent).toBe("Copy failed");
  });
});

describe("injection safety (AC 5)", () => {
  it("a hostile server detail is rendered as text", async () => {
    const { doc } = await bootLogin({
      "/api/auth/login": fakeResponse({ detail: INJECTION, code: "invalid_request" }, 400),
    });
    await signIn(doc);
    expect(byId(doc, "ao-error").textContent).toBe(INJECTION);
    expect(doc.querySelector("img")).toBeNull();
  });

  it("a hostile username is only ever sent, never rendered", async () => {
    const { doc, fetchMock } = await bootLogin({
      "/api/auth/login": fakeResponse({ detail: "nope", code: "invalid_request" }, 400),
    });
    typeInto(doc, "ao-username", INJECTION);
    typeInto(doc, "ao-password", "pw");
    await doSubmit(doc, "ao-login-form");
    expect(bodyOf(callsTo(fetchMock, "/api/auth/login")[0][1])).toEqual({ username: INJECTION, password: "pw" });
    expect(doc.querySelector("img")).toBeNull();
    expect(doc.body.textContent).not.toContain(INJECTION);
  });

  it("hostile recovery codes, secret and URI are rendered as text", async () => {
    const { doc } = await bootLogin({
      "/api/auth/status": fakeResponse(status({ state: "enrollment_required" })),
      "/api/auth/totp/enroll/begin": fakeResponse({ secret: INJECTION, otpauth_uri: INJECTION }),
      "/api/auth/totp/enroll/confirm": fakeResponse({
        state: "authenticated",
        recovery_codes: [INJECTION],
        session_proof: "P1",
      }),
    });
    typeInto(doc, "ao-enroll-token", "T");
    await doSubmit(doc, "ao-enroll-token-form");
    expect(byId(doc, "ao-enroll-uri").textContent).toBe(INJECTION);
    expect(byId(doc, "ao-enroll-secret").textContent).toBe(groupSecret(INJECTION));
    expect(doc.querySelector("img")).toBeNull();
    await doSubmit(doc, "ao-enroll-form");
    expect(doc.querySelector("#ao-recovery-list > li")?.textContent).toBe(INJECTION);
    expect(doc.querySelector("img")).toBeNull();
  });
});

describe("index page (AC 6)", () => {
  async function bootIndex(handler: Handler) {
    const fetchMock = mockFetch(handler);
    const doc = mountIndex();
    const fake = makeWin();
    initHubAuth(doc, fake.win);
    await settle();
    return { doc, fetchMock, ...fake };
  }

  const keepaliveCount = (fetchMock: ReturnType<typeof mockFetch>) =>
    callsTo(fetchMock, "/api/auth/keepalive").length;

  /** Fire `pointerdown` every `everyMs` for `totalMs`, advancing the fake clock between events. */
  async function activity(doc: Document, everyMs: number, totalMs: number, type = "pointerdown") {
    for (let t = everyMs; t <= totalMs; t += everyMs) {
      await vi.advanceTimersByTimeAsync(everyMs);
      doc.dispatchEvent(new Event(type, { bubbles: true }));
      await settle();
    }
  }

  it("logout posts with the proof, clears it and redirects to /login", async () => {
    localStorage.setItem(PROOF_KEY, "P1");
    const { doc, fetchMock, replace } = await bootIndex(() => fakeResponse({ state: "anonymous" }));
    click(doc, "ao-logout");
    await settle();
    const [[url, init]] = fetchMock.mock.calls;
    expect(url).toBe("/api/auth/logout");
    expect(init?.method).toBe("POST");
    expect(headerOf(init, PROOF_HEADER)).toBe("P1");
    expect(localStorage.getItem(PROOF_KEY)).toBeNull();
    expect(replace).toHaveBeenCalledTimes(1);
    expect(replace).toHaveBeenCalledWith("/login");
  });

  it("logout still signs out locally when the request fails", async () => {
    localStorage.setItem(PROOF_KEY, "P1");
    const { doc, replace } = await bootIndex(() => {
      throw new TypeError("offline");
    });
    click(doc, "ao-logout");
    await settle();
    expect(localStorage.getItem(PROOF_KEY)).toBeNull();
    expect(replace).toHaveBeenCalledWith("/login");
  });

  it("pointer events every 10 s for 3 minutes -> exactly 3 keepalive POSTs, each with the proof", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-05T12:00:00Z"));
    localStorage.setItem(PROOF_KEY, "P1");
    const { doc, fetchMock } = await bootIndex(() => fakeResponse({ idle_timeout_seconds: 1800 }));
    await activity(doc, 10_000, 180_000);
    expect(keepaliveCount(fetchMock)).toBe(3);
    for (const [, init] of callsTo(fetchMock, "/api/auth/keepalive")) {
      expect(init?.method).toBe("POST");
      expect(init?.body).toBeUndefined();
      expect(headerOf(init, PROOF_HEADER)).toBe("P1");
    }
  });

  it("keydown also counts as activity, and is throttled by KEEPALIVE_MIN_INTERVAL_MS", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-05T12:00:00Z"));
    const { doc, fetchMock } = await bootIndex(() => fakeResponse({}));
    doc.dispatchEvent(new Event("keydown"));
    await settle();
    await vi.advanceTimersByTimeAsync(KEEPALIVE_MIN_INTERVAL_MS - 1);
    doc.dispatchEvent(new Event("keydown"));
    await settle();
    expect(keepaliveCount(fetchMock)).toBe(1);
    await vi.advanceTimersByTimeAsync(1);
    doc.dispatchEvent(new Event("keydown"));
    await settle();
    expect(keepaliveCount(fetchMock)).toBe(2);
  });

  it("a hidden tab sends nothing", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-05T12:00:00Z"));
    const { doc, fetchMock } = await bootIndex(() => fakeResponse({}));
    setVisibility(doc, "hidden");
    await activity(doc, 10_000, 180_000);
    expect(keepaliveCount(fetchMock)).toBe(0);
  });

  it("a keepalive 401 redirects to /login", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-05T12:00:00Z"));
    const { doc, replace } = await bootIndex(() => fakeResponse({ detail: "x", code: "not_authenticated" }, 401));
    doc.dispatchEvent(new Event("pointerdown"));
    await settle();
    expect(replace).toHaveBeenCalledTimes(1);
    expect(replace).toHaveBeenCalledWith("/login");
  });

  it("a non-401 keepalive failure does not redirect", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-05T12:00:00Z"));
    const { doc, replace } = await bootIndex(() => fakeResponse({ detail: "x", code: "busy" }, 503));
    doc.dispatchEvent(new Event("pointerdown"));
    await settle();
    expect(replace).not.toHaveBeenCalled();
  });

  it("does nothing on a page that has neither #ao-auth nor #ao-logout", async () => {
    const fetchMock = mockFetch(() => fakeResponse({}));
    const doc = document.implementation.createHTMLDocument("x");
    initHubAuth(doc, makeWin().win);
    await settle();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("forbidden APIs (AC 7)", () => {
  const sources = import.meta.glob("../hub/*.ts", { query: "?raw", import: "default", eager: true }) as Record<
    string,
    string
  >;
  /** [name, pattern, a sample the pattern must catch (negative control)]. */
  const FORBIDDEN: ReadonlyArray<readonly [string, RegExp, string]> = [
    ["innerHTML", /innerHTML/, "el.innerHTML = x"],
    ["outerHTML", /outerHTML/, "el.outerHTML = x"],
    ["insertAdjacentHTML", /insertAdjacentHTML/, "el.insertAdjacentHTML('a', x)"],
    ["document.write", /document\s*\.\s*write/, "document.write(x)"],
    ["eval(", /\beval\s*\(/, "eval (x)"],
    ["new Function", /new\s+Function/, "new Function('x')"],
    ["a `mode:` key", /\bmode\s*:/, "fetch(u, { mode: 'cors' })"],
  ];

  it("scans the real hub sources", () => {
    expect(Object.keys(sources).sort()).toEqual(["../hub/hubAuth.ts", "../hub/hubAuthCore.ts"]);
  });

  it.each(FORBIDDEN)("no hub source uses %s", (_name, pattern) => {
    for (const [path, source] of Object.entries(sources)) {
      expect(pattern.test(source), path).toBe(false);
    }
  });

  it.each(FORBIDDEN)("the %s check is not vacuous (negative control)", (_name, pattern, sample) => {
    expect(pattern.test(sample)).toBe(true);
  });
});

describe("accessibility (AC 10)", () => {
  function expectAccessible(doc: Document): void {
    for (const input of Array.from(doc.querySelectorAll("input"))) {
      const labelled = input.closest("label") !== null || doc.querySelector(`label[for="${input.id}"]`) !== null;
      expect(labelled, `#${input.id} has a label`).toBe(true);
    }
    const alert = byId(doc, "ao-error");
    expect(alert.getAttribute("role")).toBe("alert");
    for (const id of ["ao-code", "ao-enroll-code"]) {
      const code = byId(doc, id);
      expect(code.getAttribute("inputmode"), id).toBe("numeric");
      expect(code.getAttribute("autocomplete"), id).toBe("one-time-code");
    }
    expect(byId(doc, "ao-password").getAttribute("autocomplete")).toBe("current-password");
    expect(byId(doc, "ao-username").getAttribute("autocomplete")).toBe("username");
  }

  it("holds on the fixture after every step, and errors land in the role=alert region", async () => {
    const { doc } = await bootLogin({
      "/api/auth/login": fakeResponse({ state: "second_factor_required", session_proof: "P1" }),
      "/api/auth/totp/verify": fakeResponse({ detail: "x", code: "enrollment_required" }, 401),
      "/api/auth/totp/enroll/begin": fakeResponse({ secret: SECRET, otpauth_uri: URI }),
      "/api/auth/totp/enroll/confirm": fakeResponse({
        state: "authenticated",
        recovery_codes: RECOVERY_CODES,
        session_proof: "P2",
      }),
    });
    expectAccessible(doc);
    await signIn(doc);
    expect(visibleStep(doc)).toBe("totp");
    expectAccessible(doc);
    await doSubmit(doc, "ao-totp-form");
    expect(visibleStep(doc)).toBe("enroll_token");
    expectAccessible(doc);
    await doSubmit(doc, "ao-enroll-token-form");
    expect(visibleStep(doc)).toBe("enroll_confirm");
    expectAccessible(doc);
    await doSubmit(doc, "ao-enroll-form");
    expect(visibleStep(doc)).toBe("codes");
    expectAccessible(doc);
    expect(byId(doc, "ao-error").closest("[role=alert]")).toBe(byId(doc, "ao-error"));
  });

  it("moves focus to the first field of each step", async () => {
    const { doc } = await bootLogin({
      "/api/auth/login": fakeResponse({ state: "second_factor_required", session_proof: "P1" }),
    });
    // createHTMLDocument documents have no browsing context, so activeElement is only reliable
    // on an attached document: assert the call by spying on focus instead.
    const focus = vi.spyOn(HTMLElement.prototype, "focus");
    await signIn(doc);
    expect(focus.mock.contexts.some((el) => (el as HTMLElement).id === "ao-code")).toBe(true);
  });
});

describe("test-double sanity", () => {
  it("the stored proof never leaks into a URL or the rendered page", async () => {
    const { doc, fetchMock } = await bootLogin({
      "/api/auth/login": fakeResponse({ state: "second_factor_required", session_proof: "SECRET-PROOF" }),
    });
    await signIn(doc);
    expect(doc.body.textContent).not.toContain("SECRET-PROOF");
    for (const [url] of fetchMock.mock.calls) expect(String(url)).not.toContain("SECRET-PROOF");
  });
});
