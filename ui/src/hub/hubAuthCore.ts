/**
 * Hub login / index page script (HLD §17.6, ADR-0021). Framework-free and dependency-free: it
 * shares NO imports with the SPA, so the protocol constants below are duplicated on purpose and
 * pinned to the SPA's by `auth-client-contract.test.ts`.
 *
 * DOM rules: text goes in through `textContent` only, visibility through `hidden`, and elements
 * are built with `createElement`. Requests use `fetch(path, {method, headers, body})` with no
 * cross-origin options, and every submit handler calls `preventDefault()`.
 */

// --- Protocol constants (HLD §2.1, §2.5, §12.6) -------------------------------------------------
export const SESSION_PROOF_HEADER = "X-AO-Session-Proof";
export const PROOF_STORAGE_KEY = "ao-session-proof";
/** 401 codes meaning "your session is gone or incomplete". Mirrors the SPA's `SESSION_LOSS_CODES`. */
export const SESSION_LOSS_CODES: readonly string[] = [
  "not_authenticated",
  "second_factor_required",
  "enrollment_required",
];
export const KEEPALIVE_MIN_INTERVAL_MS = 60_000;
export const COUNTDOWN_TICK_MS = 1000;
const COPY_FEEDBACK_MS = 2000;
const DEFAULT_LOCK_SECONDS = 1;
const SECRET_GROUP_SIZE = 4;

const STATUS_PATH = "/api/auth/status";
const LOGIN_PATH = "/api/auth/login";
const VERIFY_PATH = "/api/auth/totp/verify";
const ENROLL_BEGIN_PATH = "/api/auth/totp/enroll/begin";
const ENROLL_CONFIRM_PATH = "/api/auth/totp/enroll/confirm";
const KEEPALIVE_PATH = "/api/auth/keepalive";
const LOGOUT_PATH = "/api/auth/logout";
const HOME_URL = "/";
const LOGIN_URL = "/login";

// --- UX copy (HLD §17.8, verbatim) --------------------------------------------------------------
export const MSG_INVALID_CREDENTIALS = "Invalid username or password.";
export const MSG_SERVER_BUSY = "The server is busy. Please try again in a moment.";
export const MSG_SESSION_TIMED_OUT = "Your sign-in timed out. Please sign in again.";
export const MSG_TOTP_REQUIRED_POLICY_OFF =
  "Your account requires two-factor authentication, but enrollment is disabled on this server. Ask the operator.";
export const MSG_REPLAYED_CODE =
  "This code was already used (codes work once, across the hub and every dashboard). Wait for the next code from your authenticator app.";
export const MSG_INSECURE_TRANSPORT =
  "Setting up two-factor authentication needs a secure connection (HTTPS) or a local connection. Ask the operator to run `ao auth enable-2fa <you>` on the host, or connect over TLS.";
export const MSG_NETWORK = "Could not reach the server. Please try again.";
const MSG_USE_RECOVERY = "Use a recovery code instead";
const MSG_USE_CODE = "Use an authentication code instead";
const MSG_COPIED = "Copied";
const MSG_COPY_FAILED = "Copy failed";

export function msgTooManyAttempts(seconds: number): string {
  return `Too many attempts. Try again in ${seconds} seconds.`;
}

export function msgInvalidCode(attemptsRemaining: number | undefined): string {
  const base =
    "That code didn't work. Check the time on this computer and your phone, then try the current code.";
  return attemptsRemaining === undefined ? base : `${base} (${attemptsRemaining} attempts left)`;
}

// --- Types: the subset of HLD §2.5 this page needs ----------------------------------------------
type AuthState =
  | "disabled"
  | "anonymous"
  | "second_factor_required"
  | "enrollment_required"
  | "authenticated";

export interface HubAuthStatus {
  enabled: boolean;
  state: AuthState;
  // proxy_suspected: v2.1 (security M2). The banner keys on `!secure && !client_is_loopback`.
  transport: { secure: boolean; client_is_loopback: boolean; proxy_suspected: boolean } | null;
}

type Body = Record<string, unknown>;

interface ApiResult {
  ok: boolean;
  status: number;
  data: Body;
  retryAfter: number | undefined;
}

type Step = "login" | "totp" | "enroll_token" | "enroll_confirm" | "codes";

export function isSessionLossCode(code: string): boolean {
  return SESSION_LOSS_CODES.includes(code);
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function positiveInt(value: unknown): number | undefined {
  // `Number(null)` is 0 and `Number(undefined)` is NaN: both fall out as "not positive".
  const n = Math.ceil(Number(value));
  return Number.isFinite(n) && n > 0 ? n : undefined;
}

/** "ABCDEFGH" -> "ABCD EFGH". */
export function groupSecret(secret: string): string {
  return secret.match(new RegExp(`.{1,${SECRET_GROUP_SIZE}}`, "g"))?.join(" ") ?? secret;
}

/** Whether the plain-HTTP banner shows (HLD §2.4 E1, v2.1). */
export function showsTransportWarning(status: HubAuthStatus): boolean {
  const t = status.transport;
  return t !== null && t !== undefined && t.secure === false && t.client_is_loopback === false;
}

// --- Session proof (D25): localStorage with an in-memory fallback -------------------------------
function createProofStore(win: Window) {
  let memory: string | null = null;
  return {
    // Read on every call so a rotation made in another tab is picked up.
    read(): string | null {
      try {
        return win.localStorage.getItem(PROOF_STORAGE_KEY) ?? memory;
      } catch {
        return memory;
      }
    },
    write(proof: string): void {
      memory = proof;
      try {
        win.localStorage.setItem(PROOF_STORAGE_KEY, proof);
      } catch {
        /* storage blocked: the in-memory copy carries this page */
      }
    },
    clear(): void {
      memory = null;
      try {
        win.localStorage.removeItem(PROOF_STORAGE_KEY);
      } catch {
        /* storage blocked */
      }
    },
  };
}

type Proof = ReturnType<typeof createProofStore>;

/** One request. Stores `session_proof` from any response that carries it; never throws. */
async function call(
  proof: Proof,
  method: "GET" | "POST",
  path: string,
  body?: Body,
): Promise<ApiResult> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const current = proof.read();
  if (current) headers[SESSION_PROOF_HEADER] = current;
  try {
    const r = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let data: Body = {};
    try {
      const parsed: unknown = await r.json();
      if (parsed !== null && typeof parsed === "object") data = parsed as Body;
    } catch {
      /* non-JSON body: keep {} */
    }
    if (typeof data.session_proof === "string") proof.write(data.session_proof);
    const retryAfter =
      positiveInt(r.headers?.get("Retry-After")) ?? positiveInt(data.retry_after_seconds);
    return { ok: r.ok, status: r.status, data, retryAfter };
  } catch {
    return { ok: false, status: 0, data: {}, retryAfter: undefined };
  }
}

/** Map a failed call to the exact §17.8 string, else the server's `detail`. */
function messageFor(r: ApiResult, serverDetailForInvalidCode = false): string {
  if (r.status === 0) return MSG_NETWORK;
  const detail = str(r.data.detail);
  switch (str(r.data.code)) {
    case "invalid_credentials":
      return MSG_INVALID_CREDENTIALS;
    case "invalid_code": {
      if (serverDetailForInvalidCode && detail) return detail;
      if (r.data.reason === "replayed") return MSG_REPLAYED_CODE;
      const left = r.data.attempts_remaining;
      return msgInvalidCode(typeof left === "number" ? left : undefined);
    }
    case "busy":
    case "store_unavailable":
      return MSG_SERVER_BUSY;
    case "totp_required":
      return MSG_TOTP_REQUIRED_POLICY_OFF;
    case "insecure_transport":
      return MSG_INSECURE_TRANSPORT;
    default:
      return detail || MSG_NETWORK;
  }
}

// --- Entry point --------------------------------------------------------------------------------
export function initHubAuth(doc: Document, win: Window): void {
  const proof = createProofStore(win);
  if (doc.getElementById("ao-auth")) initLoginPage(doc, win, proof);
  if (doc.getElementById("ao-logout")) initIndexPage(doc, win, proof);
}

function initIndexPage(doc: Document, win: Window, proof: Proof): void {
  doc.getElementById("ao-logout")?.addEventListener("click", (event) => {
    event.preventDefault();
    // Signed out locally whether or not the server call worked.
    void call(proof, "POST", LOGOUT_PATH, {}).then(() => {
      proof.clear();
      win.location.replace(LOGIN_URL);
    });
  });

  let lastKeepalive: number | null = null;
  const onActivity = (): void => {
    if (doc.visibilityState !== "visible") return;
    const now = Date.now();
    if (lastKeepalive !== null && now - lastKeepalive < KEEPALIVE_MIN_INTERVAL_MS) return;
    lastKeepalive = now;
    void call(proof, "POST", KEEPALIVE_PATH).then((r) => {
      if (r.status === 401) win.location.replace(LOGIN_URL);
    });
  };
  doc.addEventListener("pointerdown", onActivity);
  doc.addEventListener("keydown", onActivity);
}

function initLoginPage(doc: Document, win: Window, proof: Proof): void {
  const el = <T extends HTMLElement>(id: string): T => doc.getElementById(id) as T;
  const errorBox = el("ao-error");
  const loginForm = el<HTMLFormElement>("ao-login-form");
  const totpForm = el<HTMLFormElement>("ao-totp-form");
  const tokenForm = el<HTMLFormElement>("ao-enroll-token-form");
  const enrollForm = el<HTMLFormElement>("ao-enroll-form");
  const enrollSection = el("ao-enroll");
  const codesSection = el("ao-recovery-codes");
  const codeLabel = el("ao-code-label");
  const recoveryLabel = el("ao-recovery-label");
  const toggleBtn = el<HTMLButtonElement>("ao-toggle-recovery");
  const secretEl = el("ao-enroll-secret");
  const uriEl = el("ao-enroll-uri");
  // The secret and URI rows sit inside <p> elements that carry no ids of their own.
  const enrollRows = [secretEl.parentElement, uriEl.parentElement];
  const codeList = el("ao-recovery-list");
  const ack = el<HTMLInputElement>("ao-codes-ack");
  const continueBtn = el<HTMLButtonElement>("ao-continue");

  let recoveryOn = false;
  let rawSecret = "";
  let countdown: ReturnType<typeof setInterval> | undefined;

  const setError = (text: string | null): void => {
    errorBox.textContent = text ?? "";
    errorBox.hidden = text === null;
  };
  const stopCountdown = (): void => {
    if (countdown !== undefined) clearInterval(countdown);
    countdown = undefined;
  };
  const submitButtons = (root: ParentNode): HTMLButtonElement[] =>
    Array.from(root.querySelectorAll<HTMLButtonElement>('button[type="submit"]'));

  function setRecoveryOn(on: boolean): void {
    recoveryOn = on;
    codeLabel.hidden = on;
    recoveryLabel.hidden = !on;
    toggleBtn.textContent = on ? MSG_USE_CODE : MSG_USE_RECOVERY;
    el(on ? "ao-recovery" : "ao-code").focus();
  }

  function show(step: Step, notice?: string): void {
    stopCountdown();
    for (const b of submitButtons(doc)) b.disabled = false;
    loginForm.hidden = step !== "login";
    totpForm.hidden = step !== "totp";
    enrollSection.hidden = step !== "enroll_token" && step !== "enroll_confirm";
    tokenForm.hidden = step !== "enroll_token";
    enrollForm.hidden = step !== "enroll_confirm";
    for (const row of enrollRows) if (row) row.hidden = step !== "enroll_confirm";
    codesSection.hidden = step !== "codes";
    setError(notice ?? null);
    if (step === "totp") setRecoveryOn(false);
    const focusId = {
      login: "ao-username",
      totp: "ao-code",
      enroll_token: "ao-enroll-token",
      enroll_confirm: "ao-enroll-code",
      codes: "ao-codes-ack",
    }[step];
    el(focusId).focus();
  }

  /** Move to the step for a server-reported state; a signed-in state leaves the page. */
  function route(state: string): void {
    if (state === "authenticated" || state === "disabled") win.location.replace(HOME_URL);
    else if (state === "second_factor_required") show("totp");
    else if (state === "enrollment_required") show("enroll_token");
    else show("login");
  }

  /** Live "try again in n seconds" lock on one form. */
  function lock(form: HTMLFormElement, seconds: number): void {
    stopCountdown();
    const buttons = submitButtons(form);
    let left = seconds;
    for (const b of buttons) b.disabled = true;
    setError(msgTooManyAttempts(left));
    countdown = setInterval(() => {
      left -= 1;
      if (left > 0) {
        setError(msgTooManyAttempts(left));
        return;
      }
      stopCountdown();
      for (const b of buttons) b.disabled = false;
      setError(null);
    }, COUNTDOWN_TICK_MS);
  }

  /** Show a failed call: lock on 429, re-route on session loss, else the mapped message. */
  function fail(r: ApiResult, form: HTMLFormElement, serverDetailForInvalidCode = false): void {
    if (r.status === 429) {
      lock(form, r.retryAfter ?? DEFAULT_LOCK_SECONDS);
      return;
    }
    const code = str(r.data.code);
    if (r.status === 401 && isSessionLossCode(code)) {
      if (code === "second_factor_required") show("totp");
      else if (code === "enrollment_required") show("enroll_token");
      else {
        proof.clear();
        show("login", MSG_SESSION_TIMED_OUT);
      }
      return;
    }
    setError(messageFor(r, serverDetailForInvalidCode));
  }

  async function refresh(): Promise<void> {
    const r = await call(proof, "GET", STATUS_PATH);
    if (!r.ok) {
      show("login", messageFor(r));
      return;
    }
    const status = r.data as unknown as HubAuthStatus;
    if (status.state === "anonymous") proof.clear();
    el("ao-transport-warning").hidden = !showsTransportWarning(status);
    route(status.state);
  }

  /** Wire a form: preventDefault, clear the error, run `handler`. */
  function onSubmit(form: HTMLFormElement, handler: () => Promise<void>): void {
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      setError(null);
      void handler();
    });
  }

  onSubmit(loginForm, async () => {
    const passwordInput = el<HTMLInputElement>("ao-password");
    const r = await call(proof, "POST", LOGIN_PATH, {
      username: el<HTMLInputElement>("ao-username").value,
      password: passwordInput.value,
    });
    passwordInput.value = "";
    if (r.ok) route(str(r.data.state));
    else fail(r, loginForm);
  });

  toggleBtn.addEventListener("click", () => setRecoveryOn(!recoveryOn));

  onSubmit(totpForm, async () => {
    const code = el<HTMLInputElement>("ao-code");
    const recovery = el<HTMLInputElement>("ao-recovery");
    const body = recoveryOn ? { recovery_code: recovery.value.trim() } : { code: code.value.trim() };
    const r = await call(proof, "POST", VERIFY_PATH, body);
    code.value = "";
    recovery.value = "";
    if (r.ok) route(str(r.data.state));
    else fail(r, totpForm);
  });

  el("ao-signout").addEventListener("click", () => {
    // Signed out locally whether or not the server call worked.
    void call(proof, "POST", LOGOUT_PATH, {}).then(() => {
      proof.clear();
      show("login");
    });
  });

  onSubmit(tokenForm, async () => {
    const tokenInput = el<HTMLInputElement>("ao-enroll-token");
    const r = await call(proof, "POST", ENROLL_BEGIN_PATH, {
      enrollment_token: tokenInput.value.trim(),
    });
    tokenInput.value = "";
    if (!r.ok) return fail(r, tokenForm, true);
    rawSecret = str(r.data.secret);
    secretEl.textContent = groupSecret(rawSecret);
    uriEl.textContent = str(r.data.otpauth_uri);
    show("enroll_confirm");
  });

  onSubmit(enrollForm, async () => {
    const codeInput = el<HTMLInputElement>("ao-enroll-code");
    const r = await call(proof, "POST", ENROLL_CONFIRM_PATH, { code: codeInput.value.trim() });
    codeInput.value = "";
    if (!r.ok) return fail(r, enrollForm);
    const codes = Array.isArray(r.data.recovery_codes) ? r.data.recovery_codes.map(str) : [];
    codeList.replaceChildren(
      ...codes.map((c) => {
        const li = doc.createElement("li");
        li.textContent = c;
        return li;
      }),
    );
    // The secret is not needed any more: do not leave it in the page.
    rawSecret = "";
    secretEl.textContent = "";
    uriEl.textContent = "";
    show("codes");
  });

  // Copy buttons: only offered where the async clipboard API exists.
  const flash = (button: HTMLElement, message: string, label: string | null): void => {
    button.textContent = message;
    setTimeout(() => {
      button.textContent = label;
    }, COPY_FEEDBACK_MS);
  };
  const wireCopy = (id: string, text: () => string): void => {
    const button = el<HTMLButtonElement>(id);
    if (typeof win.navigator?.clipboard?.writeText !== "function") {
      button.hidden = true;
      return;
    }
    const label = button.textContent;
    button.addEventListener("click", () => {
      void win.navigator.clipboard.writeText(text()).then(
        () => flash(button, MSG_COPIED, label),
        () => flash(button, MSG_COPY_FAILED, label),
      );
    });
  };
  wireCopy("ao-copy-secret", () => rawSecret);
  wireCopy("ao-copy-uri", () => uriEl.textContent ?? "");
  wireCopy("ao-copy-codes", () =>
    Array.from(codeList.children, (li) => li.textContent ?? "").join("\n"),
  );

  ack.addEventListener("change", () => {
    continueBtn.disabled = !ack.checked;
  });
  continueBtn.addEventListener("click", () => {
    if (ack.checked) win.location.replace(HOME_URL);
  });

  void refresh();
}
