import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TotpStep } from "../auth/TotpStep";
import { MSG_REPLAYED_CODE, MSG_SERVER_BUSY, msgInvalidCode, msgTooManyAttempts } from "../auth/constants";
import { bodyOf, json, mockFetch, resetAuthState, step } from "./fixtures/auth";

const FIXED_NOW = new Date("2026-10-05T12:00:00Z");

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

function renderStep(factors: ("totp" | "recovery_code")[] = ["totp", "recovery_code"]) {
  const onVerified = vi.fn();
  const onSignOut = vi.fn();
  render(<TotpStep username="alice" factors={factors} onVerified={onVerified} onSignOut={onSignOut} />);
  return { onVerified, onSignOut };
}

async function enterCode(code: string) {
  fireEvent.change(screen.getByLabelText("Authentication code"), { target: { value: code } });
  fireEvent.click(screen.getByRole("button", { name: "Verify" }));
  await settle();
}

describe("TotpStep", () => {
  it("a 6-digit submit sends {code}", async () => {
    const response = step({ used_recovery_code: false });
    const fetchMock = mockFetch(() => json(response));
    const { onVerified } = renderStep();
    await enterCode("123456");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/auth/totp/verify");
    expect(init?.method).toBe("POST");
    expect(bodyOf(init)).toEqual({ code: "123456" });
    expect(onVerified).toHaveBeenCalledWith(response);
  });

  it("strips spaces from a grouped code", async () => {
    const fetchMock = mockFetch(() => json(step()));
    renderStep();
    await enterCode("123 456");
    expect(bodyOf(fetchMock.mock.calls[0][1])).toEqual({ code: "123456" });
  });

  it("does not send a code that is not 6 digits (attempts are limited)", async () => {
    const fetchMock = mockFetch(() => json(step()));
    renderStep();
    fireEvent.change(screen.getByLabelText("Authentication code"), { target: { value: "12345" } });
    expect(screen.getByRole("button", { name: "Verify" })).toBeDisabled();
    fireEvent.submit(screen.getByRole("button", { name: "Verify" }).closest("form")!);
    await settle();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("the toggle switches to a recovery code and sends {recovery_code}", async () => {
    const fetchMock = mockFetch(() => json(step({ used_recovery_code: true })));
    const { onVerified } = renderStep();
    fireEvent.click(screen.getByRole("button", { name: "Use a recovery code instead" }));
    const input = screen.getByLabelText("Recovery code");
    expect(input).toHaveFocus();
    fireEvent.change(input, { target: { value: "ABCD-EFGH-JKMN-PQRS" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify" }));
    await settle();
    expect(bodyOf(fetchMock.mock.calls[0][1])).toEqual({ recovery_code: "ABCD-EFGH-JKMN-PQRS" });
    expect(onVerified).toHaveBeenCalledTimes(1);
  });

  it("the toggle goes back to the authentication code", () => {
    renderStep();
    fireEvent.click(screen.getByRole("button", { name: "Use a recovery code instead" }));
    fireEvent.click(screen.getByRole("button", { name: "Use an authentication code instead" }));
    expect(screen.getByLabelText("Authentication code")).toBeInTheDocument();
  });

  it("hides the recovery toggle when the server offers only TOTP", () => {
    renderStep(["totp"]);
    expect(screen.queryByRole("button", { name: /recovery code/ })).not.toBeInTheDocument();
  });

  it("invalid shows the check-your-clock string with attempts_remaining", async () => {
    mockFetch(() =>
      json({ detail: "bad", code: "invalid_code", reason: "invalid", attempts_remaining: 2 }, 401),
    );
    const { onVerified } = renderStep();
    await enterCode("123456");
    expect(screen.getByRole("alert")).toHaveTextContent(msgInvalidCode(2));
    expect(screen.getByRole("alert")).toHaveTextContent("(2 attempts left)");
    expect(onVerified).not.toHaveBeenCalled();
  });

  it("replayed shows the exact §17.8 already-used string, like the hub page (no attempts suffix)", async () => {
    mockFetch(() =>
      json({ detail: "bad", code: "invalid_code", reason: "replayed", attempts_remaining: 2 }, 401),
    );
    renderStep();
    await enterCode("123456");
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(MSG_REPLAYED_CODE);
    expect(alert.textContent).toBe(MSG_REPLAYED_CODE);
    expect(alert).not.toHaveTextContent("Check the time");
  });

  it("clears the entry after a failure so a stale code is not resent", async () => {
    mockFetch(() => json({ detail: "bad", code: "invalid_code", reason: "invalid", attempts_remaining: 1 }, 401));
    renderStep();
    await enterCode("123456");
    expect(screen.getByLabelText("Authentication code")).toHaveValue("");
  });

  it("429 locks the form and counts down on fake timers", async () => {
    mockFetch(() => json({ detail: "slow", code: "too_many_attempts" }, 429, { "Retry-After": "2" }));
    renderStep();
    await enterCode("123456");
    expect(screen.getByRole("alert")).toHaveTextContent(msgTooManyAttempts(2));
    fireEvent.change(screen.getByLabelText("Authentication code"), { target: { value: "654321" } });
    expect(screen.getByRole("button", { name: "Verify" })).toBeDisabled();
    // One tick per second: React re-renders (and re-arms the timer) between ticks.
    for (let tick = 0; tick < 2; tick += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });
    }
    expect(screen.getByRole("button", { name: "Verify" })).toBeEnabled();
    expect(screen.queryByRole("alert")).toBeEmptyDOMElement();
  });

  it("busy shows the busy string", async () => {
    mockFetch(() => json({ detail: "x", code: "store_unavailable" }, 503, { "Retry-After": "5" }));
    renderStep();
    await enterCode("123456");
    expect(screen.getByRole("alert")).toHaveTextContent(MSG_SERVER_BUSY);
  });

  it("Sign out calls the handler", () => {
    const { onSignOut } = renderStep();
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(onSignOut).toHaveBeenCalledTimes(1);
  });

  it("has a numeric one-time-code input that is focused", () => {
    renderStep();
    const input = screen.getByLabelText("Authentication code");
    expect(input).toHaveAttribute("inputmode", "numeric");
    expect(input).toHaveAttribute("autocomplete", "one-time-code");
    expect(input).toHaveFocus();
  });
});
