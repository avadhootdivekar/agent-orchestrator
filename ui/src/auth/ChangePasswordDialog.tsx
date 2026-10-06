import { useState, type FormEvent } from "react";
import { ApiError, authApi } from "../api";
import type { AuthStatus, PasswordViolation } from "../types";
import { AuthDialog } from "./AuthDialog";
import { accountErrorMessage } from "./messages";
import { useFormFailure } from "./useFormFailure";

const MSG_PASSWORDS_DIFFER = "The two new passwords do not match.";

/** Human text for each `password_policy` violation (HLD §2.2), given the server's length limits. */
function violationText(code: string, policy: AuthStatus["policy"]): string {
  switch (code as PasswordViolation) {
    case "too_short":
      return policy ? `Too short (at least ${policy.min_password_length} characters).` : "Too short.";
    case "too_long":
      return policy ? `Too long (at most ${policy.max_password_length} characters).` : "Too long.";
    case "control_characters":
      return "Must not contain control characters.";
    case "equals_username":
      return "Must not be the same as your username.";
    default:
      return code; // a violation from a newer server: show it rather than hide it
  }
}

function violationsOf(error: unknown): string[] {
  if (!(error instanceof ApiError) || error.code !== "password_policy") return [];
  const list = error.extra?.violations;
  return Array.isArray(list) ? list.filter((v): v is string => typeof v === "string") : [];
}

interface Props {
  policy: AuthStatus["policy"];
  onClose(): void;
}

/** E8: change the password. Policy hints come from `status.policy`; a 400 lists each violation. */
export function ChangePasswordDialog({ policy, onClose }: Props) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [violations, setViolations] = useState<string[]>([]);
  const [mismatch, setMismatch] = useState(false);
  const failure = useFormFailure(accountErrorMessage);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (submitting || failure.locked || !current || !next) return;
    failure.clear();
    setViolations([]);
    setMismatch(next !== again);
    if (next !== again) return;
    setSubmitting(true);
    try {
      await authApi.changePassword({ current_password: current, new_password: next });
      onClose();
    } catch (err) {
      const found = violationsOf(err);
      if (found.length > 0) setViolations(found);
      else failure.fail(err);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthDialog title="Change password">
      <form className="enroll-form" onSubmit={submit} noValidate>
        {policy && (
          <p className="auth-lede">
            Use {policy.min_password_length} to {policy.max_password_length} characters. Your other
            sessions will be signed out.
          </p>
        )}
        <div role="alert" aria-live="assertive">
          {failure.message && <div className="banner error">{failure.message}</div>}
          {mismatch && <div className="banner error">{MSG_PASSWORDS_DIFFER}</div>}
          {violations.length > 0 && (
            <div className="banner error">
              <ul className="auth-violations">
                {violations.map((v) => (
                  <li key={v}>{violationText(v, policy)}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
        <label className="auth-field">
          Current password
          <input
            name="current_password"
            type="password"
            autoComplete="current-password"
            autoFocus
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
          />
        </label>
        <label className="auth-field">
          New password
          <input
            name="new_password"
            type="password"
            autoComplete="new-password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
          />
        </label>
        <label className="auth-field">
          Repeat new password
          <input
            name="new_password_again"
            type="password"
            autoComplete="new-password"
            value={again}
            onChange={(e) => setAgain(e.target.value)}
          />
        </label>
        <div className="auth-actions">
          <button type="submit" className="primary" disabled={submitting || failure.locked || !current || !next}>
            Change password
          </button>
          <button type="button" onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </AuthDialog>
  );
}
