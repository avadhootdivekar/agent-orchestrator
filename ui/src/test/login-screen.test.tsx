import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LoginScreen, showTransportBanner } from "../auth/LoginScreen";
import {
  MSG_INVALID_CREDENTIALS,
  MSG_SERVER_BUSY,
  MSG_STORAGE_BLOCKED,
  MSG_TOTP_REQUIRED_POLICY_OFF,
  MSG_TRANSPORT_BANNER,
  msgTooManyAttempts,
} from "../auth/constants";
import { bodyOf, blockLocalStorage, headerOf, json, mockFetch, resetAuthState, step } from "./fixtures/auth";
import { readProof } from "../auth/proof";

const FIXED_NOW = new Date("2026-10-05T12:00:00Z");
const LOOPBACK = { secure: false, client_is_loopback: true, proxy_suspected: false };

beforeEach(() => {
  resetAuthState();
  vi.useFakeTimers();
  vi.setSystemTime(FIXED_NOW);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

async function settle() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

function fill(username = "alice", password = "pw-1234567890") {
  fireEvent.change(screen.getByLabelText("Username"), { target: { value: username } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: password } });
}

async function submit() {
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
  await settle();
}

function renderLogin(props: Partial<Parameters<typeof LoginScreen>[0]> = {}) {
  const onSuccess = vi.fn();
  render(<LoginScreen transport={LOOPBACK} onSuccess={onSuccess} {...props} />);
  return onSuccess;
}

describe("LoginScreen", () => {
  it("submits {username, password} and reports success with the response", async () => {
    const response = step({ session_proof: "P1" });
    const fetchMock = mockFetch(() => json(response));
    const onSuccess = renderLogin();
    fill("alice", "hunter2hunter2");
    await submit();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/auth/login");
    expect(init?.method).toBe("POST");
    expect(bodyOf(init)).toEqual({ username: "alice", password: "hunter2hunter2" });
    expect(headerOf(init, "Content-Type")).toBe("application/json");
    expect(onSuccess).toHaveBeenCalledWith("alice", response);
    expect(readProof()).toBe("P1");
  });

  it("401 shows the uniform invalid-credentials string in an alert region", async () => {
    mockFetch(() => json({ detail: "Invalid", code: "invalid_credentials" }, 401));
    const onSuccess = renderLogin();
    fill();
    await submit();
    expect(screen.getByRole("alert")).toHaveTextContent(MSG_INVALID_CREDENTIALS);
    expect(onSuccess).not.toHaveBeenCalled();
    // The form stays usable for another attempt.
    expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled();
  });

  it("429 with Retry-After: 5 disables submit and counts 5 -> 0, then re-enables", async () => {
    mockFetch(() =>
      json({ detail: "slow down", code: "too_many_attempts", retry_after_seconds: 5 }, 429, {
        "Retry-After": "5",
      }),
    );
    renderLogin();
    fill();
    await submit();
    const button = screen.getByRole("button", { name: "Sign in" });
    expect(button).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent(msgTooManyAttempts(5));

    for (const expected of [4, 3, 2, 1]) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });
      expect(screen.getByRole("alert")).toHaveTextContent(msgTooManyAttempts(expected));
      expect(button).toBeDisabled();
    }
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(button).toBeEnabled();
    expect(screen.queryByText(/Too many attempts/)).not.toBeInTheDocument();
  });

  it("does not send a second request while locked out", async () => {
    const fetchMock = mockFetch(() =>
      json({ detail: "slow", code: "too_many_attempts" }, 429, { "Retry-After": "3" }),
    );
    renderLogin();
    fill();
    await submit();
    fireEvent.submit(screen.getByRole("button", { name: "Sign in" }).closest("form")!);
    await settle();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("busy and store_unavailable show the busy string", async () => {
    mockFetch(() => json({ detail: "queue full", code: "busy" }, 503, { "Retry-After": "1" }));
    renderLogin();
    fill();
    await submit();
    expect(screen.getByRole("alert")).toHaveTextContent(MSG_SERVER_BUSY);
  });

  it("403 totp_required shows the policy-off string", async () => {
    mockFetch(() => json({ detail: "x", code: "totp_required" }, 403));
    renderLogin();
    fill();
    await submit();
    expect(screen.getByRole("alert")).toHaveTextContent(MSG_TOTP_REQUIRED_POLICY_OFF);
  });

  it("a network failure shows a generic message instead of crashing", async () => {
    mockFetch(() => {
      throw new TypeError("Failed to fetch");
    });
    renderLogin();
    fill();
    await submit();
    expect(screen.getByRole("alert")).toHaveTextContent("Could not reach the server");
  });

  it.each([
    ["insecure and not loopback", { secure: false, client_is_loopback: false, proxy_suspected: false }, true],
    ["insecure, loopback peer behind an unconfigured proxy (v2.1)", { secure: false, client_is_loopback: false, proxy_suspected: true }, true],
    ["secure", { secure: true, client_is_loopback: false, proxy_suspected: false }, false],
    ["insecure but loopback", LOOPBACK, false],
    ["no transport info", null, false],
  ])("transport banner: %s", (_name, transport, expected) => {
    renderLogin({ transport });
    expect(showTransportBanner(transport)).toBe(expected);
    if (expected) expect(screen.getByText(MSG_TRANSPORT_BANNER)).toBeInTheDocument();
    else expect(screen.queryByText(MSG_TRANSPORT_BANNER)).not.toBeInTheDocument();
  });

  it("shows the blocked-storage notice when localStorage throws", () => {
    blockLocalStorage();
    expect(readProof()).toBeNull(); // any storage access marks the page as blocked
    renderLogin();
    expect(screen.getByText(MSG_STORAGE_BLOCKED)).toBeInTheDocument();
  });

  it("does not show the blocked-storage notice normally", () => {
    renderLogin();
    expect(screen.queryByText(MSG_STORAGE_BLOCKED)).not.toBeInTheDocument();
  });

  it("shows a one-shot notice", () => {
    renderLogin({ notice: "You have been signed out." });
    expect(screen.getByRole("status")).toHaveTextContent("You have been signed out.");
  });

  it("has the right autocomplete attributes and autofocuses the first field", () => {
    renderLogin();
    const username = screen.getByLabelText("Username");
    const password = screen.getByLabelText("Password");
    expect(username).toHaveAttribute("autocomplete", "username");
    expect(password).toHaveAttribute("autocomplete", "current-password");
    expect(password).toHaveAttribute("type", "password");
    expect(username).toHaveFocus();
  });
});
