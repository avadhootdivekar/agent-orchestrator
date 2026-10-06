import { useCallback, useEffect, useMemo, useReducer, useRef, type ReactNode } from "react";
import { ApiError, authApi, setSessionLossHandler } from "../api";
import type { AuthStatus } from "../types";
import { AuthContext, type AuthContextValue } from "./context";
import { INITIAL_AUTH_VIEW, authReducer, normalizeStatus } from "./authReducer";
import { LoginScreen } from "./LoginScreen";
import { EnrollScreen } from "./EnrollScreen";
import { clearProof } from "./proof";
import { TotpStep } from "./TotpStep";
import { useKeepalive } from "./useKeepalive";

const HTTP_NOT_FOUND = 404;

/**
 * Decides what the browser may see (HLD §17.2): the dashboard only when auth is off or the session
 * is fully authenticated, a sign-in/second-factor/enroll screen otherwise. Rendering `children`
 * only in those two states means every polling hook is unmounted the instant a session is lost.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const [view, dispatch] = useReducer(authReducer, INITIAL_AUTH_VIEW);
  // Single flight (§17.4): any number of concurrent triggers share ONE status request.
  const inflight = useRef<Promise<void> | null>(null);
  // Last transport info seen, so the sign-in screen can still warn after a logout/session loss.
  const transport = useRef<AuthStatus["transport"]>(null);

  const loadStatus = useCallback((): Promise<void> => {
    if (inflight.current) return inflight.current;
    const request = authApi
      .status()
      .then(
        (raw) => {
          const status = normalizeStatus(raw);
          transport.current = status.transport;
          dispatch({ type: "STATUS_OK", status });
        },
        (error: unknown) => {
          if (error instanceof ApiError && error.status === HTTP_NOT_FOUND) {
            dispatch({ type: "STATUS_404" });
          } else {
            dispatch({
              type: "STATUS_FAILED",
              message: error instanceof Error ? error.message : undefined,
            });
          }
        },
      )
      .finally(() => {
        inflight.current = null;
      });
    inflight.current = request;
    return request;
  }, []);

  // `loading` is the single "fetch status" state: first mount, Retry, session loss, bfcache.
  useEffect(() => {
    if (view.kind === "loading") void loadStatus();
  }, [view.kind, loadStatus]);

  // The one global 401 handler. The reducer decides what a loss means in the current state.
  useEffect(() => {
    setSessionLossHandler((code) => dispatch({ type: "SESSION_LOST", code }));
    return () => setSessionLossHandler(null);
  }, []);

  // bfcache (dev-security #7): a page restored after a logout must re-check before showing data.
  useEffect(() => {
    const onPageShow = (event: PageTransitionEvent) => {
      if (event.persisted) dispatch({ type: "RECHECK" });
    };
    window.addEventListener("pageshow", onPageShow);
    return () => window.removeEventListener("pageshow", onPageShow);
  }, []);

  // A proof is meaningless once the server says anonymous (or auth is off): drop it.
  useEffect(() => {
    if (view.kind === "anonymous" || view.kind === "disabled") clearProof();
  }, [view.kind]);

  // Keepalive runs only for a signed-in session; user activity (never a timer) slides it (§17.5).
  useKeepalive(view.kind === "authenticated");

  const logout = useCallback(async (everywhere = false) => {
    try {
      await authApi.logout(everywhere);
    } catch (error) {
      // The local proof is already cleared, so this browser is signed out regardless. A failed
      // "log out everywhere" is different (other sessions may live): let the caller report it.
      if (everywhere) throw error;
    } finally {
      dispatch({ type: "LOGOUT_OK" });
    }
  }, []);

  const refresh = useCallback(() => loadStatusInPlace(dispatch), []);

  const context = useMemo<AuthContextValue>(
    () => ({
      status: view.kind === "authenticated" || view.kind === "disabled" ? view.status : null,
      refresh,
      logout,
      recoveryNotice: view.kind === "authenticated" ? (view.recoveryNotice ?? null) : null,
    }),
    [view, refresh, logout],
  );

  switch (view.kind) {
    case "loading":
      return (
        <main className="auth-screen">
          <p role="status">Loading…</p>
        </main>
      );
    case "error":
      return (
        <main className="auth-screen">
          <div className="auth-card">
            <div className="banner error" role="alert">
              {view.message}
            </div>
            <button type="button" className="primary" onClick={() => dispatch({ type: "RETRY" })}>
              Retry
            </button>
          </div>
        </main>
      );
    case "anonymous":
      return (
        <LoginScreen
          transport={transport.current}
          notice={view.notice}
          onSuccess={(username, response) => dispatch({ type: "LOGIN_OK", username, response })}
        />
      );
    case "second_factor":
      return (
        <TotpStep
          username={view.username}
          factors={view.factors}
          onVerified={(response) => dispatch({ type: "VERIFY_OK", response })}
          onSignOut={() => void logout(false)}
        />
      );
    case "enroll":
      return (
        <EnrollScreen
          mode="forced"
          onDone={() => dispatch({ type: "ENROLL_DONE" })}
          onLeave={() => void logout(false)}
        />
      );
    case "disabled":
    case "authenticated":
      return <AuthContext.Provider value={context}>{children}</AuthContext.Provider>;
  }
}

/** Background status refresh: updates the view without passing through `loading` (no unmount). */
async function loadStatusInPlace(dispatch: (event: { type: "STATUS_OK"; status: AuthStatus }) => void) {
  const status = normalizeStatus(await authApi.status());
  dispatch({ type: "STATUS_OK", status });
}
