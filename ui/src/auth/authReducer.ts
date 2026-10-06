/**
 * Pure `AuthGate` state machine (HLD §17.2): no React, no fetch.
 *
 * `<App/>` is rendered only in `disabled` and `authenticated`; every other state unmounts it,
 * which unmounts every `usePolling` hook, so a 401 polling loop is structurally impossible.
 *
 * Any event not listed for the current state returns the SAME state object, so a stale or
 * duplicated event (ten concurrent 401s, a late status response) is a no-op.
 *
 * Deliberate refinement of the §17.2 table: the session-issuing responses (login, verify,
 * enroll-confirm) do not carry `policy`/`session`/`transport`, so `LOGIN_OK(authenticated)`,
 * `VERIFY_OK` and `ENROLL_DONE` go to `loading`, which re-fetches status and lands in
 * `authenticated{status}`. A recovery-code notice rides along on `loading` until then.
 */
import {
  MSG_SESSION_TIMED_OUT,
  MSG_SIGNED_OUT,
} from "./constants";
import type { AuthStatus, AuthStepResponse, SecondFactor } from "../types";

/** Shown by T-vCgsU6's banner after a recovery-code sign-in. */
export interface RecoveryNotice {
  remaining: number;
}

export type AuthView =
  | { kind: "loading"; recoveryNotice?: RecoveryNotice }
  | { kind: "error"; message: string }
  | { kind: "disabled"; status: AuthStatus | null }
  | { kind: "anonymous"; notice?: string }
  | { kind: "second_factor"; username: string; factors: SecondFactor[] }
  | { kind: "enroll"; username: string; forced: true; enrollmentTokenRequired: boolean | null }
  | { kind: "authenticated"; status: AuthStatus; recoveryNotice?: RecoveryNotice };

export type AuthEvent =
  | { type: "STATUS_OK"; status: AuthStatus }
  | { type: "STATUS_FAILED"; message?: string }
  | { type: "STATUS_404" }
  | { type: "RETRY" }
  /** Force a fresh status fetch from any state (bfcache restore, `pageshow` persisted). */
  | { type: "RECHECK" }
  | { type: "LOGIN_OK"; username: string; response: AuthStepResponse }
  | { type: "VERIFY_OK"; response: AuthStepResponse }
  | { type: "SESSION_LOST"; code: string }
  | { type: "ENROLL_DONE" }
  | { type: "LOGOUT_OK" };

export const INITIAL_AUTH_VIEW: AuthView = { kind: "loading" };

/** Used when the server omits the factor list; TOTP is the only one that can exist without it. */
const DEFAULT_SECOND_FACTORS: SecondFactor[] = ["totp"];

const STATUS_FAILED_FALLBACK = "Could not reach the server.";

/**
 * The disabled E1 body has no `enrollment_token_required` key on old/odd servers: treat every
 * absent nullable key as null so downstream code can rely on `=== null`.
 */
export function normalizeStatus(raw: AuthStatus): AuthStatus {
  return {
    ...raw,
    user: raw.user ?? null,
    pending_username: raw.pending_username ?? null,
    second_factors: raw.second_factors ?? null,
    enrollment_token_required: raw.enrollment_token_required ?? null,
    policy: raw.policy ?? null,
    session: raw.session ?? null,
    transport: raw.transport ?? null,
  };
}

function viewForStatus(status: AuthStatus, recoveryNotice?: RecoveryNotice): AuthView {
  switch (status.state) {
    case "disabled":
      return { kind: "disabled", status };
    case "anonymous":
      return { kind: "anonymous" };
    case "second_factor_required":
      return {
        kind: "second_factor",
        username: status.pending_username ?? "",
        factors: status.second_factors ?? DEFAULT_SECOND_FACTORS,
      };
    case "enrollment_required":
      return {
        kind: "enroll",
        username: status.pending_username ?? "",
        forced: true,
        enrollmentTokenRequired: status.enrollment_token_required,
      };
    case "authenticated":
      return recoveryNotice
        ? { kind: "authenticated", status, recoveryNotice }
        : { kind: "authenticated", status };
  }
}

function recoveryNoticeFor(response: AuthStepResponse): RecoveryNotice | undefined {
  if (!response.used_recovery_code) return undefined;
  return { remaining: response.user?.recovery_codes_remaining ?? 0 };
}

export function authReducer(state: AuthView, event: AuthEvent): AuthView {
  switch (event.type) {
    case "STATUS_OK":
      // Accepted while loading, and from a background refresh in disabled/authenticated.
      // Anywhere else it is a late response for a state we already left.
      if (state.kind === "loading") return viewForStatus(event.status, state.recoveryNotice);
      if (state.kind === "authenticated") {
        return viewForStatus(event.status, state.recoveryNotice);
      }
      if (state.kind === "disabled") return viewForStatus(event.status);
      return state;

    case "STATUS_FAILED":
      return state.kind === "loading"
        ? { kind: "error", message: event.message ?? STATUS_FAILED_FALLBACK }
        : state;

    case "STATUS_404":
      // An old backend without /api/auth/status: auth cannot be on, so show the dashboard.
      return state.kind === "loading" ? { kind: "disabled", status: null } : state;

    case "RETRY":
      return state.kind === "error" ? { kind: "loading" } : state;

    case "RECHECK":
      return state.kind === "loading" ? state : { kind: "loading" };

    case "LOGIN_OK":
      if (state.kind !== "anonymous") return state;
      switch (event.response.state) {
        case "authenticated":
          return { kind: "loading" };
        case "second_factor_required":
          return {
            kind: "second_factor",
            username: event.username,
            factors: event.response.second_factors ?? DEFAULT_SECOND_FACTORS,
          };
        case "enrollment_required":
          return {
            kind: "enroll",
            username: event.username,
            forced: true,
            enrollmentTokenRequired: event.response.enrollment_token_required ?? null,
          };
        default:
          return state;
      }

    case "VERIFY_OK": {
      if (state.kind !== "second_factor") return state;
      const notice = recoveryNoticeFor(event.response);
      return notice ? { kind: "loading", recoveryNotice: notice } : { kind: "loading" };
    }

    case "SESSION_LOST":
      // A partial session ran out of time or attempts: back to the sign-in form, with a reason.
      if (state.kind === "second_factor" || state.kind === "enroll") {
        return { kind: "anonymous", notice: MSG_SESSION_TIMED_OUT };
      }
      // A full session vanished (expiry, revocation, logout elsewhere): find out why. `disabled`
      // included: a 401 there means auth was switched on under a stale page.
      if (state.kind === "authenticated" || state.kind === "disabled") return { kind: "loading" };
      return state;

    case "ENROLL_DONE":
      return state.kind === "enroll" ? { kind: "loading" } : state;

    case "LOGOUT_OK":
      return { kind: "anonymous", notice: MSG_SIGNED_OUT };
  }
}
