import { lazy, Suspense, useState, type FormEvent, type ReactNode } from "react";
import { authApi } from "../api";
import type { TotpEnrollment } from "../types";
import {
  MSG_ENROLL_TOKEN_PROMPT,
  MSG_FORCED_ENROLL_INTRO,
  SECRET_GROUP_SIZE,
  TOTP_CODE_LENGTH,
} from "./constants";
import { CopyButton } from "./CopyButton";
import { RecoveryCodes } from "./RecoveryCodes";
import { accountErrorMessage } from "./messages";
import { useFormFailure } from "./useFormFailure";

// The QR library lives in its own chunk, fetched only when an enrollment is actually shown.
const QrCode = lazy(() => import("./QrCode"));

const VOLUNTARY_INTRO =
  "Scan the QR code (or enter the setup key) in your authenticator app, then enter the 6-digit code.";

type Step =
  | { kind: "credential" }
  | { kind: "scan"; enrollment: TotpEnrollment }
  | { kind: "codes"; codes: string[] };

interface Props {
  /** `forced`: gate state `enroll`, authorised by the CLI enrollment token. `voluntary`: account menu, by password. */
  mode: "forced" | "voluntary";
  /** Called after "Continue" on the recovery-codes step. */
  onDone(): void;
  /** Sign out (forced) or Cancel (voluntary). */
  onLeave(): void;
}

/** "JBSWY3DP…" -> "JBSW Y3DP …" for reading aloud / typing by hand. */
export function groupSecret(secret: string): string {
  return secret.match(new RegExp(`.{1,${SECRET_GROUP_SIZE}}`, "g"))?.join(" ") ?? secret;
}

/**
 * TOTP enrollment (HLD §2.4 E4/E5, §14.3). Forced mode asks for the one-time CLI enrollment token
 * first, voluntary mode for the current password; the two are never sent together. Both then show
 * the QR (lazy), the grouped secret and the URI as text, ask for a confirming code, and finish
 * with the recovery codes behind an acknowledgement gate.
 *
 * Wrong-credential errors stay on the first step (the form is re-shown with the message);
 * `insecure_transport` is shown with no QR because begin never succeeded.
 */
export function EnrollScreen({ mode, onDone, onLeave }: Props) {
  const forced = mode === "forced";
  const [step, setStep] = useState<Step>({ kind: "credential" });
  const [credential, setCredential] = useState("");
  const [code, setCode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const failure = useFormFailure(accountErrorMessage);

  async function begin(event: FormEvent) {
    event.preventDefault();
    if (submitting || failure.locked || !credential) return;
    setSubmitting(true);
    failure.clear();
    try {
      const enrollment = await authApi.enrollBegin(
        forced ? { enrollment_token: credential.trim() } : { current_password: credential },
      );
      setCredential("");
      setStep({ kind: "scan", enrollment });
    } catch (err) {
      failure.fail(err);
    } finally {
      setSubmitting(false);
    }
  }

  async function confirm(event: FormEvent) {
    event.preventDefault();
    const digits = code.replace(/\s+/g, "");
    if (submitting || failure.locked || digits.length !== TOTP_CODE_LENGTH) return;
    setSubmitting(true);
    failure.clear();
    try {
      const response = await authApi.enrollConfirm({ code: digits });
      setStep({ kind: "codes", codes: response.recovery_codes ?? [] });
    } catch (err) {
      failure.fail(err);
      setCode("");
    } finally {
      setSubmitting(false);
    }
  }

  const alert = (
    <div role="alert" aria-live="assertive">
      {failure.message && <div className="banner error">{failure.message}</div>}
    </div>
  );

  let body: ReactNode;
  if (step.kind === "credential") {
    body = (
      <form className="enroll-form" onSubmit={begin} noValidate>
        <p className="auth-lede">{forced ? MSG_ENROLL_TOKEN_PROMPT : "Confirm your password to set up two-factor authentication."}</p>
        {alert}
        {forced ? (
          <label className="auth-field">
            Enrollment token
            <input
              name="enrollment_token"
              autoComplete="off"
              autoFocus
              value={credential}
              onChange={(e) => setCredential(e.target.value)}
            />
          </label>
        ) : (
          <label className="auth-field">
            Current password
            <input
              name="current_password"
              type="password"
              autoComplete="current-password"
              autoFocus
              value={credential}
              onChange={(e) => setCredential(e.target.value)}
            />
          </label>
        )}
        <div className="auth-actions">
          <button type="submit" className="primary" disabled={submitting || failure.locked || !credential}>
            Continue
          </button>
          <button type="button" onClick={onLeave}>
            {forced ? "Sign out" : "Cancel"}
          </button>
        </div>
      </form>
    );
  } else if (step.kind === "scan") {
    const { enrollment } = step;
    body = (
      <form className="enroll-form" onSubmit={confirm} noValidate>
        <p className="auth-lede">{forced ? MSG_FORCED_ENROLL_INTRO : VOLUNTARY_INTRO}</p>
        <div className="qr-box">
          <Suspense fallback={<p role="status">Loading QR code…</p>}>
            <QrCode uri={enrollment.otpauth_uri} />
          </Suspense>
        </div>
        <div className="auth-secret">
          <span>Setup key</span>
          <code data-testid="enroll-secret">{groupSecret(enrollment.secret)}</code>
          <CopyButton text={enrollment.secret} />
        </div>
        <div className="auth-secret">
          <span>Setup URI</span>
          <code data-testid="enroll-uri">{enrollment.otpauth_uri}</code>
          <CopyButton text={enrollment.otpauth_uri} />
        </div>
        {alert}
        <label className="auth-field">
          Authentication code
          <input
            name="code"
            inputMode="numeric"
            autoComplete="one-time-code"
            autoFocus
            maxLength={TOTP_CODE_LENGTH + 1}
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
        </label>
        <div className="auth-actions">
          <button
            type="submit"
            className="primary"
            disabled={submitting || failure.locked || code.replace(/\s+/g, "").length !== TOTP_CODE_LENGTH}
          >
            Confirm
          </button>
          <button type="button" onClick={onLeave}>
            {forced ? "Sign out" : "Cancel"}
          </button>
        </div>
      </form>
    );
  } else {
    body = <RecoveryCodes codes={step.codes} onContinue={onDone} />;
  }

  if (!forced) return <div className="enroll">{body}</div>;
  return (
    <main className="auth-screen">
      <div className="auth-card enroll">
        <h1>Set up two-factor authentication</h1>
        {body}
      </div>
    </main>
  );
}
