import { useEffect } from "react";

/** Default dashboard poll cadence (run page, runs list). */
export const POLL_MS = 3000;

/**
 * Run `tick` immediately and then every `intervalMs`, but ONLY while the page is visible and
 * `enabled` (E-iafh2F FR-6/FR-10): a background browser tab, or (Phase 2) an inactive in-app
 * tab, makes no requests at all. Becoming visible/enabled again ticks right away so the view
 * is fresh the moment the user returns.
 *
 * `tick` must be referentially stable (wrap in `useCallback`); a new identity restarts the
 * loop, which is how a changed `runId` triggers an immediate refetch.
 */
export function usePolling(
  tick: () => void | Promise<void>,
  intervalMs: number,
  enabled = true,
): void {
  useEffect(() => {
    if (!enabled) return undefined;
    let timer: ReturnType<typeof setInterval> | null = null;
    const run = () => void tick();
    const start = () => {
      if (timer === null) timer = setInterval(run, intervalMs);
    };
    const stop = () => {
      if (timer !== null) {
        clearInterval(timer);
        timer = null;
      }
    };
    const onVisibility = () => {
      if (document.hidden) {
        stop();
      } else {
        run();
        start();
      }
    };
    if (!document.hidden) {
      run();
      start();
    }
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [tick, intervalMs, enabled]);
}
