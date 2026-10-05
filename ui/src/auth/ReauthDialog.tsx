import { useState, type FormEvent, type ReactNode } from "react";
import type { ReauthBody } from "../types";
import { AuthDialog } from "./AuthDialog";
import { accountErrorMessage } from "./messages";
import { useFormFailure } from "./useFormFailure";

interface Props<T> {
  title: string;
  lede: string;
  submitLabel: string;
  /** The wire call (E6 / E7). A rejection is shown in the dialog; the dialog stays open. */
  action(body: ReauthBody): Promise<T>;
  /** Called with the 200 body. Return a node to keep the dialog open showing it (the new codes). */
  onSuccess(result: T): ReactNode | void;
  onCancel(): void;
}

/**
 * "Current password + authentication code" dialog shared by Disable (E6) and Regenerate (E7).
 * The code field takes a TOTP code or a recovery code, as the server accepts either.
 */
export function ReauthDialog<T>({ title, lede, submitLabel, action, onSuccess, onCancel }: Props<T>) {
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<ReactNode>(null);
  const failure = useFormFailure(accountErrorMessage);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (submitting || failure.locked || !password || !code.trim()) return;
    setSubmitting(true);
    failure.clear();
    try {
      const outcome = onSuccess(await action({ current_password: password, code: code.trim() }));
      if (outcome) setResult(outcome);
    } catch (err) {
      failure.fail(err);
      setCode("");
    } finally {
      setSubmitting(false);
    }
  }

  if (result) return <AuthDialog title={title}>{result}</AuthDialog>;
  return (
    <AuthDialog title={title}>
      <form className="enroll-form" onSubmit={submit} noValidate>
        <p className="auth-lede">{lede}</p>
        <div role="alert" aria-live="assertive">
          {failure.message && <div className="banner error">{failure.message}</div>}
        </div>
        <label className="auth-field">
          Current password
          <input
            name="current_password"
            type="password"
            autoComplete="current-password"
            autoFocus
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        <label className="auth-field">
          Authentication or recovery code
          <input
            name="code"
            autoComplete="one-time-code"
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
        </label>
        <div className="auth-actions">
          <button
            type="submit"
            className="primary"
            disabled={submitting || failure.locked || !password || !code.trim()}
          >
            {submitLabel}
          </button>
          <button type="button" onClick={onCancel}>
            Cancel
          </button>
        </div>
      </form>
    </AuthDialog>
  );
}
