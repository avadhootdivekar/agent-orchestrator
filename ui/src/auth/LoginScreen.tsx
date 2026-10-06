import { useState, type FormEvent } from "react";
import { authApi } from "../api";
import type { AuthStatus, AuthStepResponse } from "../types";
import { MSG_STORAGE_BLOCKED, MSG_TRANSPORT_BANNER } from "./constants";
import { proofStorageBlocked } from "./proof";
import { useFormFailure } from "./useFormFailure";

/** True when credentials would cross an unencrypted, non-local connection (FR-21, v2.1 incl. proxies). */
export function showTransportBanner(transport: AuthStatus["transport"]): boolean {
  return transport !== null && !transport.secure && !transport.client_is_loopback;
}

interface Props {
  transport: AuthStatus["transport"];
  /** A one-shot reason for being here ("You have been signed out.", the timeout notice, ...). */
  notice?: string;
  onSuccess(username: string, response: AuthStepResponse): void;
}

/** Username + password step. The wire contract is HLD §2.4 E2; the strings are §17.8. */
export function LoginScreen({ transport, notice, onSuccess }: Props) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const failure = useFormFailure();
  const locked = failure.locked;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (submitting || locked) return;
    setSubmitting(true);
    failure.clear();
    try {
      const response = await authApi.login(username, password);
      setPassword("");
      onSuccess(username, response);
    } catch (err) {
      failure.fail(err);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="auth-screen">
      <form className="auth-card" onSubmit={submit} noValidate>
        <h1>Sign in</h1>
        {showTransportBanner(transport) && (
          <div className="banner error" data-testid="transport-banner">
            {MSG_TRANSPORT_BANNER}
          </div>
        )}
        {proofStorageBlocked() && <div className="banner info">{MSG_STORAGE_BLOCKED}</div>}
        {notice && (
          <div className="banner info" role="status">
            {notice}
          </div>
        )}
        <div role="alert" aria-live="assertive">
          {failure.message && <div className="banner error">{failure.message}</div>}
        </div>
        <label className="auth-field">
          Username
          <input
            name="username"
            autoComplete="username"
            autoFocus
            required
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
        </label>
        <label className="auth-field">
          Password
          <input
            name="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        <button type="submit" className="primary" disabled={submitting || locked || !username}>
          Sign in
        </button>
      </form>
    </main>
  );
}
