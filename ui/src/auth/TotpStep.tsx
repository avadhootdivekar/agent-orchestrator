import { useState, type FormEvent } from "react";
import { authApi } from "../api";
import type { AuthStepResponse, SecondFactor } from "../types";
import { TOTP_CODE_LENGTH } from "./constants";
import { useFormFailure } from "./useFormFailure";

/** Longest accepted entry: "123 456" for a TOTP code, "ABCD-EFGH-JKMN-PQRS" for a recovery code. */
const TOTP_INPUT_MAX_LENGTH = 7;
const RECOVERY_INPUT_MAX_LENGTH = 24;

interface Props {
  username: string;
  factors: SecondFactor[];
  onVerified(response: AuthStepResponse): void;
  onSignOut(): void;
}

/**
 * Second-factor step (HLD §2.4 E3). A 401 `not_authenticated` (the partial session timed out or ran
 * out of attempts) is handled globally by `AuthGate` via the session-loss handler, which returns to
 * sign-in with the timeout notice; this component only shows per-attempt errors.
 */
export function TotpStep({ username, factors, onVerified, onSignOut }: Props) {
  const [recoveryMode, setRecoveryMode] = useState(false);
  const [value, setValue] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const failure = useFormFailure();
  const locked = failure.locked;
  const canUseRecovery = factors.includes("recovery_code");
  // Codes are single-use and attempts are limited, so do not let an obviously short code go out.
  const digits = value.replace(/\D/g, "");
  const valid = recoveryMode ? value.trim().length > 0 : digits.length === TOTP_CODE_LENGTH;

  function toggle() {
    setRecoveryMode((on) => !on);
    setValue("");
    failure.clear();
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (submitting || locked || !valid) return;
    setSubmitting(true);
    failure.clear();
    try {
      const response = recoveryMode
        ? await authApi.verifyRecovery(value.trim())
        : await authApi.verifyTotp(value.replace(/\s+/g, ""));
      onVerified(response);
    } catch (err) {
      failure.fail(err);
      setValue("");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="auth-screen">
      <form className="auth-card" onSubmit={submit} noValidate>
        <h1>Two-factor authentication</h1>
        {username && <p className="auth-lede">Signing in as {username}.</p>}
        <div role="alert" aria-live="assertive">
          {failure.message && <div className="banner error">{failure.message}</div>}
        </div>
        {recoveryMode ? (
          <label className="auth-field" key="recovery">
            Recovery code
            <input
              name="recovery_code"
              autoComplete="off"
              autoFocus
              maxLength={RECOVERY_INPUT_MAX_LENGTH}
              value={value}
              onChange={(e) => setValue(e.target.value)}
            />
          </label>
        ) : (
          <label className="auth-field" key="code">
            Authentication code
            <input
              name="code"
              inputMode="numeric"
              autoComplete="one-time-code"
              autoFocus
              maxLength={TOTP_INPUT_MAX_LENGTH}
              value={value}
              onChange={(e) => setValue(e.target.value)}
            />
          </label>
        )}
        <div className="auth-actions">
          <button type="submit" className="primary" disabled={submitting || locked || !valid}>
            Verify
          </button>
          {canUseRecovery && (
            <button type="button" className="link" onClick={toggle}>
              {recoveryMode ? "Use an authentication code instead" : "Use a recovery code instead"}
            </button>
          )}
          <button type="button" onClick={onSignOut}>
            Sign out
          </button>
        </div>
      </form>
    </main>
  );
}
