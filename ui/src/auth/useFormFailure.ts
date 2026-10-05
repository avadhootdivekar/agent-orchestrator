import { useCallback, useEffect, useState } from "react";
import { COUNTDOWN_TICK_MS, msgTooManyAttempts } from "./constants";
import { authErrorMessage, lockoutSeconds } from "./messages";

/**
 * Whole-second countdown for the 429 notice. `start(n)` begins at n; it ticks once per second
 * (a chained timeout, so fake timers advance it deterministically) down to 0, where it stops.
 */
export function useCountdown(): { remaining: number; start: (seconds: number) => void } {
  const [remaining, setRemaining] = useState(0);

  useEffect(() => {
    if (remaining <= 0) return undefined;
    const timer = setTimeout(() => setRemaining((n) => Math.max(0, n - 1)), COUNTDOWN_TICK_MS);
    return () => clearTimeout(timer);
  }, [remaining]);

  const start = useCallback((seconds: number) => setRemaining(Math.max(0, Math.floor(seconds))), []);
  return { remaining, start };
}

/**
 * Error + 429-lockout state shared by the sign-in and second-factor forms. A lockout is shown as a
 * live countdown (and disappears at 0); any other failure is shown as the §17.8 message.
 */
export function useFormFailure() {
  const [error, setError] = useState<string | null>(null);
  const lockout = useCountdown();

  const fail = useCallback(
    (err: unknown) => {
      const seconds = lockoutSeconds(err);
      if (seconds > 0) {
        setError(null);
        lockout.start(seconds);
      } else {
        setError(authErrorMessage(err));
      }
    },
    [lockout.start],
  );
  const clear = useCallback(() => setError(null), []);

  const locked = lockout.remaining > 0;
  const message = locked ? msgTooManyAttempts(lockout.remaining) : error;
  return { message, locked, fail, clear };
}
