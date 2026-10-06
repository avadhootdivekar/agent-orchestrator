import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthGate } from "../auth/AuthGate";
import {
  BLOB_URL_REVOKE_DELAY_MS,
  MSG_ENROLL_TOKEN_PROMPT,
  MSG_FORCED_ENROLL_INTRO,
  MSG_INSECURE_TRANSPORT,
  msgInvalidCode,
} from "../auth/constants";
import { EnrollScreen, groupSecret } from "../auth/EnrollScreen";
import { AUTHENTICATED_STATUS, USER, bodyOf, callsTo, headerOf, json, mockFetch, resetAuthState, status, step } from "./fixtures/auth";

// The real QR library is exercised in qr-code.test.tsx; here the lazy chunk is a marker element.
vi.mock("../auth/QrCode", () => ({
  default: ({ uri }: { uri: string }) => <div data-testid="qr-code" data-uri={uri} />,
}));

const STATUS = "/api/auth/status";
const BEGIN = "/api/auth/totp/enroll/begin";
const CONFIRM = "/api/auth/totp/enroll/confirm";
const SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP";
const URI =
  "otpauth://totp/ao%40devbox:alice?secret=JBSWY3DPEHPK3PXP&issuer=ao%40devbox&algorithm=SHA1&digits=6&period=30";
const ENROLLMENT = {
  secret: SECRET,
  otpauth_uri: URI,
  issuer: "ao@devbox",
  account: "alice",
  algorithm: "SHA1",
  digits: 6,
  period: 30,
};
const CODES = Array.from({ length: 10 }, (_, i) => `ABCD-EFGH-JKM${i}-PQRS`);

beforeEach(() => resetAuthState());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

function type(field: string | RegExp | HTMLElement, value: string) {
  const element = field instanceof HTMLElement ? field : screen.getByLabelText(field);
  fireEvent.change(element, { target: { value } });
}

const click = (name: string | RegExp) => fireEvent.click(screen.getByRole("button", { name }));

describe("groupSecret", () => {
  it("groups in 4s and keeps a short tail", () => {
    expect(groupSecret("JBSWY3DPEH")).toBe("JBSW Y3DP EH");
    expect(groupSecret("")).toBe("");
  });
});

describe("EnrollScreen — forced (gate state enroll)", () => {
  function renderForced(fetchHandler: Parameters<typeof mockFetch>[0]) {
    const fetchMock = mockFetch(fetchHandler);
    render(
      <AuthGate>
        <div data-testid="app" />
      </AuthGate>,
    );
    return fetchMock;
  }

  it("token first, begin with exactly {enrollment_token}, QR + grouped secret + URI, confirm, 10 codes, gated Continue", async () => {
    let enrolled = false;
    const fetchMock = renderForced((url) => {
      if (url === STATUS) {
        return json(
          enrolled
            ? AUTHENTICATED_STATUS
            : status({ state: "enrollment_required", pending_username: "alice", enrollment_token_required: true }),
        );
      }
      if (url === BEGIN) return json(ENROLLMENT);
      if (url === CONFIRM) {
        enrolled = true;
        return json(step({ user: { ...USER, totp_enrolled: true }, recovery_codes: CODES, session_proof: "P2" }));
      }
      return json([]);
    });

    // The token field comes first, with the §17.8 prompt, and nothing else yet.
    expect(await screen.findByLabelText("Enrollment token")).toBeInTheDocument();
    expect(screen.getByText(MSG_ENROLL_TOKEN_PROMPT)).toBeInTheDocument();
    expect(screen.queryByLabelText("Current password")).not.toBeInTheDocument();
    expect(screen.queryByTestId("qr-code")).not.toBeInTheDocument();

    type("Enrollment token", " ABCD-EFGH-JKMN-PQRS ");
    click("Continue");

    expect(await screen.findByTestId("qr-code")).toHaveAttribute("data-uri", URI);
    expect(screen.getByText(MSG_FORCED_ENROLL_INTRO)).toBeInTheDocument();
    expect(screen.getByTestId("enroll-secret").textContent).toBe(groupSecret(SECRET));
    expect(screen.getByTestId("enroll-uri").textContent).toBe(URI);
    const begins = callsTo(fetchMock, BEGIN);
    expect(begins).toHaveLength(1);
    expect(bodyOf(begins[0][1])).toEqual({ enrollment_token: "ABCD-EFGH-JKMN-PQRS" });

    type("Authentication code", "123 456");
    click("Confirm");

    const list = await screen.findByRole("list", { name: "Recovery codes" });
    expect(list.querySelectorAll("li")).toHaveLength(10);
    expect(bodyOf(callsTo(fetchMock, CONFIRM)[0][1])).toEqual({ code: "123456" });
    // The confirm response rotated the proof: it is stored, and the NEXT call carries it.
    const cont = screen.getByRole("button", { name: "Continue" });
    expect(cont).toBeDisabled();
    expect(screen.queryByTestId("app")).not.toBeInTheDocument();

    fireEvent.click(screen.getByLabelText("I have stored these codes"));
    expect(cont).toBeEnabled();
    fireEvent.click(cont);

    expect(await screen.findByTestId("app")).toBeInTheDocument();
    const lastStatus = callsTo(fetchMock, STATUS).at(-1)!;
    expect(headerOf(lastStatus[1], "X-AO-Session-Proof")).toBe("P2");
  });

  it("403 insecure_transport on begin shows the exact §17.8 string and no QR", async () => {
    renderForced((url) => {
      if (url === STATUS) return json(status({ state: "enrollment_required", pending_username: "alice" }));
      if (url === BEGIN) return json({ detail: "plain http", code: "insecure_transport" }, 403);
      return json([]);
    });
    type(await screen.findByLabelText("Enrollment token"), "ABCD");
    click("Continue");
    expect(await screen.findByRole("alert")).toHaveTextContent(MSG_INSECURE_TRANSPORT);
    expect(screen.queryByTestId("qr-code")).not.toBeInTheDocument();
    expect(screen.queryByTestId("enroll-secret")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Enrollment token")).toBeInTheDocument();
  });

  it("401 invalid_code on begin (bad token) shows the 'That code didn't work' string and stays on the token step", async () => {
    renderForced((url) => {
      if (url === STATUS) return json(status({ state: "enrollment_required", pending_username: "alice" }));
      if (url === BEGIN) {
        return json({ detail: "bad token", code: "invalid_code", reason: "invalid", attempts_remaining: 4 }, 401);
      }
      return json([]);
    });
    type(await screen.findByLabelText("Enrollment token"), "WRONG");
    click("Continue");
    expect(await screen.findByRole("alert")).toHaveTextContent(msgInvalidCode(4));
    expect(screen.getByLabelText("Enrollment token")).toBeInTheDocument();
  });

  it("a wrong confirm code shows the error and keeps the QR on screen", async () => {
    renderForced((url) => {
      if (url === STATUS) return json(status({ state: "enrollment_required", pending_username: "alice" }));
      if (url === BEGIN) return json(ENROLLMENT);
      if (url === CONFIRM) {
        return json({ detail: "bad", code: "invalid_code", reason: "invalid", attempts_remaining: 2 }, 401);
      }
      return json([]);
    });
    type(await screen.findByLabelText("Enrollment token"), "ABCD");
    click("Continue");
    await screen.findByTestId("qr-code");
    type("Authentication code", "000000");
    click("Confirm");
    expect(await screen.findByRole("alert")).toHaveTextContent(msgInvalidCode(2));
    expect(screen.getByTestId("qr-code")).toBeInTheDocument();
  });
});

describe("EnrollScreen — voluntary (account menu)", () => {
  it("asks for the password first and begins with exactly {current_password}", async () => {
    const fetchMock = mockFetch((url) => (url === BEGIN ? json(ENROLLMENT) : json({})));
    render(<EnrollScreen mode="voluntary" onDone={vi.fn()} onLeave={vi.fn()} />);
    expect(screen.queryByLabelText("Enrollment token")).not.toBeInTheDocument();
    type("Current password", "hunter2hunter2");
    click("Continue");
    expect(await screen.findByTestId("qr-code")).toBeInTheDocument();
    expect(bodyOf(callsTo(fetchMock, BEGIN)[0][1])).toEqual({ current_password: "hunter2hunter2" });
  });

  it("401 invalid_credentials shows the error and stays on the password step", async () => {
    mockFetch(() => json({ detail: "no", code: "invalid_credentials" }, 401));
    const onLeave = vi.fn();
    render(<EnrollScreen mode="voluntary" onDone={vi.fn()} onLeave={onLeave} />);
    type("Current password", "wrong");
    click("Continue");
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid username or password.");
    expect(screen.getByLabelText("Current password")).toBeInTheDocument();
    expect(screen.queryByTestId("qr-code")).not.toBeInTheDocument();
    click("Cancel");
    expect(onLeave).toHaveBeenCalledTimes(1);
  });
});

describe("RecoveryCodes actions", () => {
  async function toCodesStep() {
    mockFetch((url) => {
      if (url === BEGIN) return json(ENROLLMENT);
      if (url === CONFIRM) return json(step({ recovery_codes: CODES }));
      return json({});
    });
    const onDone = vi.fn();
    render(<EnrollScreen mode="voluntary" onDone={onDone} onLeave={vi.fn()} />);
    type("Current password", "pw");
    click("Continue");
    await screen.findByTestId("qr-code");
    type("Authentication code", "123456");
    click("Confirm");
    await screen.findByRole("list", { name: "Recovery codes" });
    return onDone;
  }

  it("Copy all writes the codes, one per line, to the clipboard", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    await toCodesStep();
    click("Copy all");
    await screen.findByText("Copied");
    expect(writeText).toHaveBeenCalledWith(CODES.join("\n"));
  });

  it("a denied clipboard says so instead of failing silently", async () => {
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText: vi.fn().mockRejectedValue(new Error("denied")) } });
    await toCodesStep();
    click("Copy all");
    expect(await screen.findByText(/Copy failed/)).toBeInTheDocument();
  });

  it("Download .txt makes a Blob URL and revokes it after the delay (fake timers)", async () => {
    const create = vi.fn(() => "blob:codes");
    const revoke = vi.fn();
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke }));
    const clicked: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push(`${this.href}|${this.download}`);
    });
    await toCodesStep();
    vi.useFakeTimers();
    click("Download .txt");
    expect(create).toHaveBeenCalledTimes(1);
    expect(clicked).toEqual(["blob:codes|ao-recovery-codes.txt"]);
    const blob = (create.mock.calls[0] as unknown as [Blob])[0];
    // jsdom's Blob has no text(): size + type pin the content (one code per line, trailing newline).
    expect(blob.type).toBe("text/plain");
    expect(blob.size).toBe(new TextEncoder().encode(`${CODES.join("\n")}\n`).length);
    expect(revoke).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(BLOB_URL_REVOKE_DELAY_MS);
    });
    expect(revoke).toHaveBeenCalledWith("blob:codes");
  });

  it("Continue calls onDone only after the acknowledgement", async () => {
    const onDone = await toCodesStep();
    expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(onDone).not.toHaveBeenCalled();
    fireEvent.click(screen.getByLabelText("I have stored these codes"));
    click("Continue");
    expect(onDone).toHaveBeenCalledTimes(1);
  });
});
