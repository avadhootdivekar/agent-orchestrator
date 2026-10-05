import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AccountMenu, RecoveryBanner } from "../auth/AccountMenu";
import { AuthGate } from "../auth/AuthGate";
import { MSG_INSECURE_TRANSPORT, MSG_SIGNED_OUT, PROOF_STORAGE_KEY, msgRecoveryUsed } from "../auth/constants";
import { AuthContext, type AuthContextValue } from "../auth/context";
import { LAST_LAUNCH_KEY_PREFIX } from "../launch";
import { STORAGE_KEY as TABS_KEY } from "../tabs/model";
import type { AuthUser } from "../types";
import {
  AUTHENTICATED_STATUS,
  USER,
  blockLocalStorage,
  bodyOf,
  callsTo,
  json,
  mockFetch,
  resetAuthState,
  status,
} from "./fixtures/auth";

const STATUS = "/api/auth/status";
const CODES = Array.from({ length: 10 }, (_, i) => `ABCD-EFGH-JKM${i}-PQRS`);

beforeEach(() => resetAuthState());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  sessionStorage.clear();
});

const click = (name: string | RegExp) => fireEvent.click(screen.getByRole("button", { name }));
const type = (label: string | RegExp, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });

function userWith(overrides: Partial<AuthUser>): AuthUser {
  return { ...USER, ...overrides };
}

/** AccountMenu under a hand-built context (no gate): `logout`/`refresh` are spies. */
function renderMenu(user: AuthUser, extra: Partial<AuthContextValue> = {}) {
  const value: AuthContextValue = {
    status: { ...AUTHENTICATED_STATUS, user },
    refresh: vi.fn().mockResolvedValue(undefined),
    logout: vi.fn().mockResolvedValue(undefined),
    recoveryNotice: null,
    ...extra,
  };
  render(
    <AuthContext.Provider value={value}>
      <AccountMenu />
      <RecoveryBanner />
    </AuthContext.Provider>,
  );
  return value;
}

const openMenu = () => fireEvent.click(screen.getByRole("button", { name: /alice/ }));

describe("AccountMenu — visibility", () => {
  it("renders nothing when auth is off or the user is unknown", () => {
    const { container } = render(
      <AuthContext.Provider value={{ status: null, refresh: vi.fn(), logout: vi.fn(), recoveryNotice: null }}>
        <AccountMenu />
      </AuthContext.Provider>,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the username and auth method", () => {
    renderMenu(userWith({ auth_method: "password+totp", totp_enrolled: true }));
    openMenu();
    expect(screen.getByText(/Signed in as/)).toHaveTextContent("alice");
    expect(screen.getByText(/Password \+ authenticator/)).toBeInTheDocument();
    expect(screen.getByText("Log out")).toBeInTheDocument();
    expect(screen.getByText("Log out everywhere")).toBeInTheDocument();
  });

  it("offers Enable only when can_enroll_totp, Disable only when enrolled and can_disable_totp, Regenerate when enrolled", () => {
    renderMenu(userWith({ can_enroll_totp: true, totp_enrolled: false }));
    openMenu();
    expect(screen.getByRole("button", { name: "Enable two-factor" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Disable two-factor" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Regenerate recovery codes" })).not.toBeInTheDocument();
    cleanup();

    renderMenu(userWith({ totp_enrolled: true, can_enroll_totp: false, can_disable_totp: false, totp_required: true }));
    openMenu();
    expect(screen.queryByRole("button", { name: "Enable two-factor" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Disable two-factor" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Regenerate recovery codes" })).toBeInTheDocument();
    cleanup();

    renderMenu(userWith({ totp_enrolled: true, can_enroll_totp: false, can_disable_totp: true }));
    openMenu();
    expect(screen.getByRole("button", { name: "Disable two-factor" })).toBeInTheDocument();
  });
});

describe("Change password", () => {
  it("400 password_policy shows the policy hint and each violation; a 200 closes the dialog", async () => {
    let attempt = 0;
    const fetchMock = mockFetch(() => {
      attempt += 1;
      return attempt === 1
        ? json({ detail: "policy", code: "password_policy", violations: ["too_short", "equals_username"] }, 400)
        : json({ state: "authenticated", user: USER, session_proof: "P2" });
    });
    renderMenu(USER);
    openMenu();
    click("Change password");
    const dialog = screen.getByRole("dialog", { name: "Change password" });
    expect(within(dialog).getByText(/Use 12 to 256 characters/)).toBeInTheDocument();

    type("Current password", "old-password-1");
    type("New password", "short");
    type("Repeat new password", "short");
    click("Change password"); // submit (the menu button is gone; this is the dialog's)
    expect(await screen.findByText("Too short (at least 12 characters).")).toBeInTheDocument();
    expect(screen.getByText("Must not be the same as your username.")).toBeInTheDocument();
    expect(bodyOf(callsTo(fetchMock, "/api/auth/password")[0][1])).toEqual({
      current_password: "old-password-1",
      new_password: "short",
    });

    type("New password", "a-much-longer-password");
    type("Repeat new password", "a-much-longer-password");
    click("Change password");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("does not send when the two new passwords differ", async () => {
    const fetchMock = mockFetch(() => json({}));
    renderMenu(USER);
    openMenu();
    click("Change password");
    type("Current password", "old-password-1");
    type("New password", "a-much-longer-password");
    type("Repeat new password", "different-longer-password");
    click("Change password");
    expect(await screen.findByText("The two new passwords do not match.")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("Two-factor dialogs", () => {
  it("Disable: 403 totp_required shows the server detail", async () => {
    mockFetch(() => json({ detail: "Your policy requires 2FA.", code: "totp_required" }, 403));
    renderMenu(userWith({ totp_enrolled: true, can_disable_totp: true }));
    openMenu();
    click("Disable two-factor");
    type("Current password", "pw");
    type("Authentication or recovery code", "123456");
    click("Disable");
    expect(await screen.findByRole("alert")).toHaveTextContent("Your policy requires 2FA.");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("Disable: success sends password + code, refreshes status and closes", async () => {
    const fetchMock = mockFetch(() => json({ state: "authenticated", user: USER, session_proof: "P2" }));
    const ctx = renderMenu(userWith({ totp_enrolled: true, can_disable_totp: true }));
    openMenu();
    click("Disable two-factor");
    type("Current password", "pw");
    type("Authentication or recovery code", " 123456 ");
    click("Disable");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(bodyOf(callsTo(fetchMock, "/api/auth/totp/disable")[0][1])).toEqual({
      current_password: "pw",
      code: "123456",
    });
    expect(ctx.refresh).toHaveBeenCalledTimes(1);
  });

  it("Regenerate: the new codes appear behind the acknowledgement gate", async () => {
    mockFetch(() => json({ recovery_codes: CODES, user: USER, session_proof: "P2" }));
    const ctx = renderMenu(userWith({ totp_enrolled: true }));
    openMenu();
    click("Regenerate recovery codes");
    type("Current password", "pw");
    type("Authentication or recovery code", "123456");
    click("Regenerate");
    const list = await screen.findByRole("list", { name: "Recovery codes" });
    expect(list.querySelectorAll("li")).toHaveLength(10);
    expect(ctx.refresh).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
    fireEvent.click(screen.getByLabelText("I have stored these codes"));
    click("Continue");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("Regenerate: 403 insecure_transport shows the exact §17.8 string", async () => {
    mockFetch(() => json({ detail: "plain http", code: "insecure_transport" }, 403));
    renderMenu(userWith({ totp_enrolled: true }));
    openMenu();
    click("Regenerate recovery codes");
    type("Current password", "pw");
    type("Authentication or recovery code", "123456");
    click("Regenerate");
    expect(await screen.findByRole("alert")).toHaveTextContent(MSG_INSECURE_TRANSPORT);
  });

  it("Enable opens the voluntary enrollment, which asks for the password first", () => {
    renderMenu(userWith({ can_enroll_totp: true }));
    openMenu();
    click("Enable two-factor");
    expect(screen.getByRole("dialog", { name: "Enable two-factor authentication" })).toBeInTheDocument();
    expect(screen.getByLabelText("Current password")).toBeInTheDocument();
  });
});

describe("Logout (through the real AuthGate)", () => {
  function renderGate(fetchHandler?: Parameters<typeof mockFetch>[0]) {
    const fetchMock = mockFetch(
      fetchHandler ??
        ((url) => {
          if (url === STATUS) return json(status({ state: "authenticated", user: USER }));
          if (url === "/api/auth/logout") return json({ state: "anonymous" });
          return json([]);
        }),
    );
    render(
      <AuthGate>
        <AccountMenu />
      </AuthGate>,
    );
    return fetchMock;
  }

  function seedClientState() {
    localStorage.setItem(PROOF_STORAGE_KEY, "P1");
    localStorage.setItem(TABS_KEY, '{"v":1}');
    sessionStorage.setItem(`${LAST_LAUNCH_KEY_PREFIX}runs`, "launch-1");
    sessionStorage.setItem(`${LAST_LAUNCH_KEY_PREFIX}workflows`, "launch-2");
    sessionStorage.setItem("unrelated", "keep");
  }

  it("Log out everywhere posts {everywhere: true}", async () => {
    const fetchMock = renderGate();
    fireEvent.click(await screen.findByRole("button", { name: /alice/ }));
    click("Log out everywhere");
    expect(await screen.findByText(MSG_SIGNED_OUT)).toBeInTheDocument();
    expect(bodyOf(callsTo(fetchMock, "/api/auth/logout")[0][1])).toEqual({ everywhere: true });
  });

  it("plain Log out posts {} and clears ao-tabs, the last-launch keys and the proof", async () => {
    seedClientState();
    const fetchMock = renderGate();
    fireEvent.click(await screen.findByRole("button", { name: /alice/ }));
    click("Log out");
    expect(await screen.findByText(MSG_SIGNED_OUT)).toBeInTheDocument();
    expect(bodyOf(callsTo(fetchMock, "/api/auth/logout")[0][1])).toEqual({});
    expect(localStorage.getItem(TABS_KEY)).toBeNull();
    expect(localStorage.getItem(PROOF_STORAGE_KEY)).toBeNull();
    expect(sessionStorage.getItem(`${LAST_LAUNCH_KEY_PREFIX}runs`)).toBeNull();
    expect(sessionStorage.getItem(`${LAST_LAUNCH_KEY_PREFIX}workflows`)).toBeNull();
    expect(sessionStorage.getItem("unrelated")).toBe("keep");
  });

  it("still logs out when storage throws (guarded)", async () => {
    seedClientState();
    const fetchMock = renderGate();
    fireEvent.click(await screen.findByRole("button", { name: /alice/ }));
    blockLocalStorage();
    click("Log out");
    expect(await screen.findByText(MSG_SIGNED_OUT)).toBeInTheDocument();
    expect(callsTo(fetchMock, "/api/auth/logout")).toHaveLength(1);
  });

  it("a failed 'log out everywhere' is reported (other sessions may live)", async () => {
    const ctx = renderMenu(USER, { logout: vi.fn().mockRejectedValue(new Error("x")) });
    openMenu();
    click("Log out everywhere");
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not reach the server");
    expect(ctx.logout).toHaveBeenCalledWith(true);
  });
});

describe("RecoveryBanner", () => {
  it("shows the §17.8 recovery-login string with {n}, and is dismissible", () => {
    renderMenu(USER, { recoveryNotice: { remaining: 9 } });
    expect(screen.getByRole("status")).toHaveTextContent(msgRecoveryUsed(9));
    click("Dismiss");
    expect(screen.queryByText(msgRecoveryUsed(9))).not.toBeInTheDocument();
  });

  it("renders nothing without a notice", () => {
    renderMenu(USER);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
