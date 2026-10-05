import { describe, expect, it } from "vitest";
import {
  INITIAL_AUTH_VIEW,
  authReducer,
  normalizeStatus,
  type AuthEvent,
  type AuthView,
} from "../auth/authReducer";
import { MSG_SESSION_TIMED_OUT, MSG_SIGNED_OUT } from "../auth/constants";
import {
  ANONYMOUS_STATUS,
  AUTHENTICATED_STATUS,
  DISABLED_STATUS,
  USER,
  status,
  step,
} from "./fixtures/auth";

const LOADING: AuthView = { kind: "loading" };
const ERROR: AuthView = { kind: "error", message: "boom" };
const DISABLED: AuthView = { kind: "disabled", status: DISABLED_STATUS };
const ANONYMOUS: AuthView = { kind: "anonymous" };
const SECOND: AuthView = { kind: "second_factor", username: "alice", factors: ["totp"] };
const ENROLL: AuthView = {
  kind: "enroll",
  username: "alice",
  forced: true,
  enrollmentTokenRequired: true,
};
const AUTHED: AuthView = { kind: "authenticated", status: AUTHENTICATED_STATUS };

const SECOND_STATUS = status({
  state: "second_factor_required",
  pending_username: "alice",
  second_factors: ["totp", "recovery_code"],
});
const ENROLL_STATUS = status({
  state: "enrollment_required",
  pending_username: "alice",
  enrollment_token_required: true,
});

interface Row {
  name: string;
  from: AuthView;
  event: AuthEvent;
  to: AuthView;
}

/** One row per line of the HLD §17.2 table (plus the refinements documented in authReducer.ts). */
const TABLE: Row[] = [
  // loading + STATUS_OK(s) -> per s.state
  { name: "loading STATUS_OK disabled", from: LOADING, event: { type: "STATUS_OK", status: DISABLED_STATUS }, to: { kind: "disabled", status: DISABLED_STATUS } },
  { name: "loading STATUS_OK anonymous", from: LOADING, event: { type: "STATUS_OK", status: ANONYMOUS_STATUS }, to: { kind: "anonymous" } },
  {
    name: "loading STATUS_OK second_factor_required",
    from: LOADING,
    event: { type: "STATUS_OK", status: SECOND_STATUS },
    to: { kind: "second_factor", username: "alice", factors: ["totp", "recovery_code"] },
  },
  {
    name: "loading STATUS_OK enrollment_required",
    from: LOADING,
    event: { type: "STATUS_OK", status: ENROLL_STATUS },
    to: { kind: "enroll", username: "alice", forced: true, enrollmentTokenRequired: true },
  },
  { name: "loading STATUS_OK authenticated", from: LOADING, event: { type: "STATUS_OK", status: AUTHENTICATED_STATUS }, to: AUTHED },
  {
    name: "loading(recovery notice) STATUS_OK authenticated keeps the notice",
    from: { kind: "loading", recoveryNotice: { remaining: 3 } },
    event: { type: "STATUS_OK", status: AUTHENTICATED_STATUS },
    to: { kind: "authenticated", status: AUTHENTICATED_STATUS, recoveryNotice: { remaining: 3 } },
  },
  { name: "loading STATUS_FAILED", from: LOADING, event: { type: "STATUS_FAILED", message: "boom" }, to: ERROR },
  {
    name: "loading STATUS_FAILED without a message",
    from: LOADING,
    event: { type: "STATUS_FAILED" },
    to: { kind: "error", message: "Could not reach the server." },
  },
  { name: "loading STATUS_404 (old backend)", from: LOADING, event: { type: "STATUS_404" }, to: { kind: "disabled", status: null } },
  { name: "error RETRY", from: ERROR, event: { type: "RETRY" }, to: LOADING },
  // anonymous + LOGIN_OK
  { name: "anonymous LOGIN_OK authenticated", from: ANONYMOUS, event: { type: "LOGIN_OK", username: "alice", response: step() }, to: LOADING },
  {
    name: "anonymous LOGIN_OK second_factor_required",
    from: ANONYMOUS,
    event: {
      type: "LOGIN_OK",
      username: "alice",
      response: step({ state: "second_factor_required", second_factors: ["totp", "recovery_code"] }),
    },
    to: { kind: "second_factor", username: "alice", factors: ["totp", "recovery_code"] },
  },
  {
    name: "anonymous LOGIN_OK enrollment_required",
    from: ANONYMOUS,
    event: {
      type: "LOGIN_OK",
      username: "alice",
      response: step({ state: "enrollment_required", enrollment_token_required: true }),
    },
    to: { kind: "enroll", username: "alice", forced: true, enrollmentTokenRequired: true },
  },
  // second_factor + VERIFY_OK
  { name: "second_factor VERIFY_OK", from: SECOND, event: { type: "VERIFY_OK", response: step() }, to: LOADING },
  {
    name: "second_factor VERIFY_OK with a recovery code",
    from: SECOND,
    event: {
      type: "VERIFY_OK",
      response: step({
        used_recovery_code: true,
        user: { ...USER, recovery_codes_remaining: 9 },
      }),
    },
    to: { kind: "loading", recoveryNotice: { remaining: 9 } },
  },
  // SESSION_LOST
  { name: "second_factor SESSION_LOST", from: SECOND, event: { type: "SESSION_LOST", code: "not_authenticated" }, to: { kind: "anonymous", notice: MSG_SESSION_TIMED_OUT } },
  { name: "enroll SESSION_LOST", from: ENROLL, event: { type: "SESSION_LOST", code: "not_authenticated" }, to: { kind: "anonymous", notice: MSG_SESSION_TIMED_OUT } },
  { name: "authenticated SESSION_LOST", from: AUTHED, event: { type: "SESSION_LOST", code: "not_authenticated" }, to: LOADING },
  { name: "disabled SESSION_LOST (auth switched on under a stale page)", from: DISABLED, event: { type: "SESSION_LOST", code: "not_authenticated" }, to: LOADING },
  // enroll + ENROLL_DONE
  { name: "enroll ENROLL_DONE", from: ENROLL, event: { type: "ENROLL_DONE" }, to: LOADING },
  // refresh + recheck
  {
    name: "authenticated STATUS_OK (in-place refresh) replaces status",
    from: { kind: "authenticated", status: AUTHENTICATED_STATUS, recoveryNotice: { remaining: 2 } },
    event: { type: "STATUS_OK", status: { ...AUTHENTICATED_STATUS, policy: null } },
    to: { kind: "authenticated", status: { ...AUTHENTICATED_STATUS, policy: null }, recoveryNotice: { remaining: 2 } },
  },
  { name: "authenticated STATUS_OK anonymous (refresh found the session gone)", from: AUTHED, event: { type: "STATUS_OK", status: ANONYMOUS_STATUS }, to: ANONYMOUS },
  { name: "authenticated RECHECK", from: AUTHED, event: { type: "RECHECK" }, to: LOADING },
  { name: "disabled RECHECK", from: DISABLED, event: { type: "RECHECK" }, to: LOADING },
  { name: "second_factor RECHECK", from: SECOND, event: { type: "RECHECK" }, to: LOADING },
  // any + LOGOUT_OK
  ...(
    [
      ["loading", LOADING],
      ["error", ERROR],
      ["disabled", DISABLED],
      ["anonymous", ANONYMOUS],
      ["second_factor", SECOND],
      ["enroll", ENROLL],
      ["authenticated", AUTHED],
    ] as const
  ).map(([name, from]): Row => ({
    name: `${name} LOGOUT_OK`,
    from,
    event: { type: "LOGOUT_OK" },
    to: { kind: "anonymous", notice: MSG_SIGNED_OUT },
  })),
];

describe("authReducer — HLD §17.2 transition table", () => {
  it("starts in loading", () => {
    expect(INITIAL_AUTH_VIEW).toEqual({ kind: "loading" });
  });

  it.each(TABLE)("$name", ({ from, event, to }) => {
    expect(authReducer(from, event)).toEqual(to);
  });
});

describe("authReducer — unexpected events leave the SAME state object", () => {
  const OTHER: Row[] = [
    { name: "late STATUS_OK while anonymous", from: ANONYMOUS, event: { type: "STATUS_OK", status: AUTHENTICATED_STATUS }, to: ANONYMOUS },
    { name: "late STATUS_OK while in second factor", from: SECOND, event: { type: "STATUS_OK", status: ANONYMOUS_STATUS }, to: SECOND },
    { name: "STATUS_FAILED outside loading", from: AUTHED, event: { type: "STATUS_FAILED" }, to: AUTHED },
    { name: "STATUS_404 outside loading", from: AUTHED, event: { type: "STATUS_404" }, to: AUTHED },
    { name: "RETRY outside error", from: LOADING, event: { type: "RETRY" }, to: LOADING },
    { name: "RECHECK while already loading", from: LOADING, event: { type: "RECHECK" }, to: LOADING },
    { name: "LOGIN_OK outside anonymous", from: AUTHED, event: { type: "LOGIN_OK", username: "a", response: step() }, to: AUTHED },
    { name: "VERIFY_OK outside second_factor", from: ANONYMOUS, event: { type: "VERIFY_OK", response: step() }, to: ANONYMOUS },
    { name: "ENROLL_DONE outside enroll", from: AUTHED, event: { type: "ENROLL_DONE" }, to: AUTHED },
    { name: "SESSION_LOST while loading", from: LOADING, event: { type: "SESSION_LOST", code: "not_authenticated" }, to: LOADING },
    { name: "SESSION_LOST while anonymous", from: ANONYMOUS, event: { type: "SESSION_LOST", code: "not_authenticated" }, to: ANONYMOUS },
    { name: "SESSION_LOST while in error", from: ERROR, event: { type: "SESSION_LOST", code: "not_authenticated" }, to: ERROR },
  ];

  it.each(OTHER)("$name", ({ from, event }) => {
    expect(authReducer(from, event)).toBe(from);
  });

  it("ten concurrent SESSION_LOST events collapse into one transition", () => {
    let state: AuthView = AUTHED;
    const seen: AuthView[] = [];
    for (let i = 0; i < 10; i += 1) {
      state = authReducer(state, { type: "SESSION_LOST", code: "not_authenticated" });
      seen.push(state);
    }
    expect(seen[0]).toEqual(LOADING);
    for (const s of seen.slice(1)) expect(s).toBe(seen[0]);
  });
});

describe("normalizeStatus", () => {
  it("treats a missing enrollment_token_required (old disabled body) as null", () => {
    const { enrollment_token_required: _omitted, ...rest } = DISABLED_STATUS;
    const normalized = normalizeStatus(rest as typeof DISABLED_STATUS);
    expect(normalized.enrollment_token_required).toBeNull();
    expect(normalized.transport).toBeNull();
  });

  it("keeps present values untouched", () => {
    expect(normalizeStatus(ENROLL_STATUS)).toEqual(ENROLL_STATUS);
  });
});
