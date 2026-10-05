import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { api } from "../api";
import { AuthGate } from "../auth/AuthGate";
import { useAuth } from "../auth/context";
import {
  MSG_FORCED_ENROLL_INTRO,
  MSG_SESSION_TIMED_OUT,
  MSG_SIGNED_OUT,
  PROOF_STORAGE_KEY,
  msgRecoveryUsed,
} from "../auth/constants";
import {
  ANONYMOUS_STATUS,
  AUTHENTICATED_STATUS,
  DISABLED_STATUS,
  USER,
  callsTo,
  headerOf,
  json,
  mockFetch,
  resetAuthState,
  status,
  step,
} from "./fixtures/auth";

const STATUS_PATH = "/api/auth/status";

/** Stands in for the dashboard: fetches on mount like a polling view would. */
function Probe() {
  useEffect(() => {
    void api.runs().catch(() => undefined);
  }, []);
  return <div data-testid="app">dashboard</div>;
}

function renderGate() {
  return render(
    <AuthGate>
      <Probe />
    </AuthGate>,
  );
}

beforeEach(() => resetAuthState());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("AuthGate — auth disabled (NFR-1)", () => {
  it("renders the dashboard after exactly one extra request, with no proof header", async () => {
    const fetchMock = mockFetch((url) => (url === STATUS_PATH ? json(DISABLED_STATUS) : json([])));
    renderGate();
    expect(await screen.findByTestId("app")).toBeInTheDocument();
    // Passive effects can lag the DOM commit outside act(): wait for the dashboard's own call.
    await waitFor(() => expect(callsTo(fetchMock, "/api/runs")).toHaveLength(1));
    expect(callsTo(fetchMock, STATUS_PATH)).toHaveLength(1);
    expect(headerOf(callsTo(fetchMock, STATUS_PATH)[0][1], "X-AO-Session-Proof")).toBeUndefined();
    // status + the Probe's own /api/runs and nothing else
    expect(fetchMock.mock.calls.map(([u]) => String(u)).sort()).toEqual(["/api/auth/status", "/api/runs"]);
  });

  it("renders the real <App/> unchanged", async () => {
    mockFetch((url) => {
      if (url === STATUS_PATH) return json(DISABLED_STATUS);
      if (url === "/api/workspace") return json({ workspace_root: "/ws" });
      if (url === "/api/runs") return json([]);
      return json({ detail: "nope" }, 404);
    });
    render(
      <AuthGate>
        <App />
      </AuthGate>,
    );
    expect(await screen.findByText("Agent Orchestrator")).toBeInTheDocument();
    expect(await screen.findByText("/ws")).toBeInTheDocument();
  });

  it("treats a 404 from an old backend as disabled", async () => {
    const fetchMock = mockFetch((url) =>
      url === STATUS_PATH ? json({ detail: "Not Found" }, 404) : json([]),
    );
    renderGate();
    expect(await screen.findByTestId("app")).toBeInTheDocument();
    expect(callsTo(fetchMock, STATUS_PATH)).toHaveLength(1);
  });

  it("clears a stale stored proof when the server says auth is off", async () => {
    localStorage.setItem(PROOF_STORAGE_KEY, "STALE");
    mockFetch((url) => (url === STATUS_PATH ? json(DISABLED_STATUS) : json([])));
    renderGate();
    await screen.findByTestId("app");
    expect(localStorage.getItem(PROOF_STORAGE_KEY)).toBeNull();
  });
});

describe("AuthGate — other states", () => {
  it("anonymous shows the login screen and never mounts the dashboard", async () => {
    const fetchMock = mockFetch((url) => (url === STATUS_PATH ? json(ANONYMOUS_STATUS) : json([])));
    renderGate();
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByTestId("app")).not.toBeInTheDocument();
    expect(callsTo(fetchMock, "/api/runs")).toHaveLength(0);
  });

  it("anonymous drops a stored proof", async () => {
    localStorage.setItem(PROOF_STORAGE_KEY, "STALE");
    mockFetch(() => json(ANONYMOUS_STATUS));
    renderGate();
    await screen.findByRole("heading", { name: "Sign in" });
    expect(localStorage.getItem(PROOF_STORAGE_KEY)).toBeNull();
  });

  it("a network failure shows an error with Retry, and Retry re-fetches", async () => {
    let fail = true;
    const fetchMock = mockFetch((url) => {
      if (url !== STATUS_PATH) return json([]);
      if (fail) throw new TypeError("Failed to fetch");
      return json(DISABLED_STATUS);
    });
    renderGate();
    expect(await screen.findByRole("alert")).toHaveTextContent("Failed to fetch");
    expect(callsTo(fetchMock, STATUS_PATH)).toHaveLength(1);
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByTestId("app")).toBeInTheDocument();
    expect(callsTo(fetchMock, STATUS_PATH)).toHaveLength(2);
  });

  it("enrollment_required shows the forced-enrollment placeholder with Sign out", async () => {
    const fetchMock = mockFetch((url) => {
      if (url === STATUS_PATH) {
        return json(status({ state: "enrollment_required", pending_username: "alice" }));
      }
      if (url === "/api/auth/logout") return json({ state: "anonymous" });
      return json([]);
    });
    renderGate();
    expect(await screen.findByText(MSG_FORCED_ENROLL_INTRO)).toBeInTheDocument();
    expect(screen.queryByTestId("app")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByText(MSG_SIGNED_OUT)).toBeInTheDocument();
    expect(callsTo(fetchMock, "/api/auth/logout")).toHaveLength(1);
  });

  it("second_factor_required shows the second-factor step", async () => {
    mockFetch(() =>
      json(
        status({
          state: "second_factor_required",
          pending_username: "alice",
          second_factors: ["totp", "recovery_code"],
        }),
      ),
    );
    renderGate();
    expect(await screen.findByLabelText("Authentication code")).toBeInTheDocument();
    expect(screen.queryByTestId("app")).not.toBeInTheDocument();
  });
});

describe("AuthGate — bfcache (pageshow)", () => {
  function pageshow(persisted: boolean) {
    const event = new Event("pageshow");
    Object.defineProperty(event, "persisted", { value: persisted });
    act(() => {
      window.dispatchEvent(event);
    });
  }

  it("persisted:true re-fetches status exactly once; persisted:false does not", async () => {
    const fetchMock = mockFetch((url) => (url === STATUS_PATH ? json(DISABLED_STATUS) : json([])));
    renderGate();
    await screen.findByTestId("app");
    expect(callsTo(fetchMock, STATUS_PATH)).toHaveLength(1);

    pageshow(false);
    expect(callsTo(fetchMock, STATUS_PATH)).toHaveLength(1);

    pageshow(true);
    await waitFor(() => expect(callsTo(fetchMock, STATUS_PATH)).toHaveLength(2));
    await screen.findByTestId("app");
    expect(callsTo(fetchMock, STATUS_PATH)).toHaveLength(2);
  });

  it("a session logged out while the page sat in the bfcache lands on the login screen", async () => {
    let signedIn = true;
    mockFetch((url) =>
      url === STATUS_PATH ? json(signedIn ? AUTHENTICATED_STATUS : ANONYMOUS_STATUS) : json([]),
    );
    renderGate();
    await screen.findByTestId("app");
    signedIn = false;
    pageshow(true);
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByTestId("app")).not.toBeInTheDocument();
  });
});

describe("AuthGate — full flows", () => {
  it("password login -> authenticated (status is re-fetched for the session)", async () => {
    let signedIn = false;
    const fetchMock = mockFetch((url) => {
      if (url === STATUS_PATH) return json(signedIn ? AUTHENTICATED_STATUS : ANONYMOUS_STATUS);
      if (url === "/api/auth/login") {
        signedIn = true;
        return json(step({ session_proof: "P1" }));
      }
      return json([]);
    });
    const user = userEvent.setup();
    renderGate();
    await user.type(await screen.findByLabelText("Username"), "alice");
    await user.type(screen.getByLabelText("Password"), "hunter2hunter2");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByTestId("app")).toBeInTheDocument();
    expect(callsTo(fetchMock, STATUS_PATH)).toHaveLength(2);
    // The proof from the login response rides on the follow-up status and dashboard calls.
    expect(headerOf(callsTo(fetchMock, STATUS_PATH)[1][1], "X-AO-Session-Proof")).toBe("P1");
    expect(headerOf(callsTo(fetchMock, "/api/runs")[0][1], "X-AO-Session-Proof")).toBe("P1");
  });

  it("login -> second factor -> recovery code exposes recoveryNotice.remaining via context", async () => {
    let signedIn = false;
    mockFetch((url, init) => {
      if (url === STATUS_PATH) return json(signedIn ? AUTHENTICATED_STATUS : ANONYMOUS_STATUS);
      if (url === "/api/auth/login") {
        return json(step({ state: "second_factor_required", second_factors: ["totp", "recovery_code"], user: undefined }));
      }
      if (url === "/api/auth/totp/verify") {
        expect(JSON.parse(String(init?.body))).toEqual({ recovery_code: "ABCD-EFGH-JKMN-PQRS" });
        signedIn = true;
        return json(
          step({
            used_recovery_code: true,
            user: { ...USER, recovery_codes_remaining: 9 },
            session_proof: "P2",
          }),
        );
      }
      return json([]);
    });
    function Notice() {
      const { recoveryNotice } = useAuth();
      return <div data-testid="notice">{recoveryNotice ? msgRecoveryUsed(recoveryNotice.remaining) : "none"}</div>;
    }
    const user = userEvent.setup();
    render(
      <AuthGate>
        <Notice />
      </AuthGate>,
    );
    await user.type(await screen.findByLabelText("Username"), "alice");
    await user.type(screen.getByLabelText("Password"), "pw");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await user.click(await screen.findByRole("button", { name: "Use a recovery code instead" }));
    await user.type(screen.getByLabelText("Recovery code"), "ABCD-EFGH-JKMN-PQRS");
    await user.click(screen.getByRole("button", { name: "Verify" }));
    expect(await screen.findByTestId("notice")).toHaveTextContent(msgRecoveryUsed(9));
  });

  it("a 401 not_authenticated during the second-factor step returns to login with the timeout notice", async () => {
    mockFetch((url) => {
      if (url === STATUS_PATH) {
        return json(
          status({ state: "second_factor_required", pending_username: "alice", second_factors: ["totp"] }),
        );
      }
      if (url === "/api/auth/totp/verify") {
        return json({ detail: "Not authenticated", code: "not_authenticated" }, 401);
      }
      return json([]);
    });
    const user = userEvent.setup();
    renderGate();
    await user.type(await screen.findByLabelText("Authentication code"), "123456");
    await user.click(screen.getByRole("button", { name: "Verify" }));
    expect(await screen.findByText(MSG_SESSION_TIMED_OUT)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });

  it("session loss while authenticated unmounts the dashboard and re-fetches status", async () => {
    let signedIn = true;
    const fetchMock = mockFetch((url) => {
      if (url === STATUS_PATH) return json(signedIn ? AUTHENTICATED_STATUS : ANONYMOUS_STATUS);
      if (url === "/api/runs") {
        signedIn = false;
        return json({ detail: "Not authenticated", code: "not_authenticated" }, 401);
      }
      return json([]);
    });
    renderGate();
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByTestId("app")).not.toBeInTheDocument();
    expect(callsTo(fetchMock, STATUS_PATH)).toHaveLength(2);
  });

  it("logout clears the proof and shows the signed-out notice", async () => {
    localStorage.setItem(PROOF_STORAGE_KEY, "P1");
    const fetchMock = mockFetch((url) => {
      if (url === STATUS_PATH) return json(AUTHENTICATED_STATUS);
      if (url === "/api/auth/logout") return json({ state: "anonymous" });
      return json([]);
    });
    function Out() {
      const { logout } = useAuth();
      return <button onClick={() => void logout(true)}>out</button>;
    }
    render(
      <AuthGate>
        <Out />
      </AuthGate>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "out" }));
    expect(await screen.findByText(MSG_SIGNED_OUT)).toBeInTheDocument();
    expect(localStorage.getItem(PROOF_STORAGE_KEY)).toBeNull();
    const logoutCall = callsTo(fetchMock, "/api/auth/logout")[0][1];
    expect(headerOf(logoutCall, "X-AO-Session-Proof")).toBe("P1");
    expect(JSON.parse(String(logoutCall?.body))).toEqual({ everywhere: true });
  });

  it("shows the transport banner on the login screen from the status transport", async () => {
    mockFetch(() =>
      json(
        status({
          transport: { secure: false, client_is_loopback: false, proxy_suspected: true },
        }),
      ),
    );
    renderGate();
    expect(await screen.findByTestId("transport-banner")).toBeInTheDocument();
  });
});
