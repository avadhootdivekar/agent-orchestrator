/** Shared builders for the auth vitest files (mocked `fetch`, HLD §2 payloads). */
import { vi } from "vitest";
import { resetProofStateForTests } from "../../auth/proof";
import type { AuthStatus, AuthStepResponse, AuthUser } from "../../types";

export function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

export const USER: AuthUser = {
  username: "alice",
  auth_method: "password",
  roles: [],
  totp_enrolled: false,
  recovery_codes_remaining: null,
  totp_required: false,
  can_enroll_totp: true,
  can_disable_totp: false,
};

const POLICY = { totp: "optional", min_password_length: 12, max_password_length: 256 } as const;
const LOOPBACK = { secure: false, client_is_loopback: true, proxy_suspected: false };
const SESSION = {
  idle_timeout_seconds: 1800,
  idle_expires_at: "2026-10-05T12:30:00Z",
  absolute_expires_at: "2026-10-06T00:00:00Z",
};

/** The E1 body with auth off — keys in the documented order. */
export const DISABLED_STATUS: AuthStatus = {
  enabled: false,
  state: "disabled",
  user: null,
  pending_username: null,
  second_factors: null,
  enrollment_token_required: null,
  policy: null,
  session: null,
  transport: null,
};

export function status(overrides: Partial<AuthStatus> = {}): AuthStatus {
  return {
    enabled: true,
    state: "anonymous",
    user: null,
    pending_username: null,
    second_factors: null,
    enrollment_token_required: null,
    policy: POLICY,
    session: null,
    transport: LOOPBACK,
    ...overrides,
  };
}

export const ANONYMOUS_STATUS = status();
export const AUTHENTICATED_STATUS = status({ state: "authenticated", user: USER, session: SESSION });

export function step(overrides: Partial<AuthStepResponse> = {}): AuthStepResponse {
  return { state: "authenticated", user: USER, session_proof: "P1", ...overrides };
}

export type Handler = (url: string, init: RequestInit | undefined) => Response | Promise<Response>;

/** Stub `fetch` with `handler`; returns the mock for call inspection. */
export function mockFetch(handler: Handler) {
  const fn = vi.fn(async (url: string, init?: RequestInit) => handler(String(url), init));
  vi.stubGlobal("fetch", fn);
  return fn;
}

export function callsTo(fn: ReturnType<typeof mockFetch>, path: string) {
  return fn.mock.calls.filter(([url]) => String(url) === path);
}

export function headerOf(init: RequestInit | undefined, name: string): string | undefined {
  return (init?.headers as Record<string, string> | undefined)?.[name];
}

export function bodyOf(init: RequestInit | undefined): unknown {
  return init?.body === undefined ? undefined : JSON.parse(String(init.body));
}

/** Clean slate: no stored proof, no memory proof, no blocked-storage flag. */
export function resetAuthState(): void {
  localStorage.clear();
  resetProofStateForTests();
}

/** Make every localStorage access throw (private mode / blocked site data). */
export function blockLocalStorage() {
  return [
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    }),
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    }),
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    }),
  ];
}
