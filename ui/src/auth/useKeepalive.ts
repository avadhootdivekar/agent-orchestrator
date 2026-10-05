import { useEffect } from "react";
import { authApi } from "../api";
import { KEEPALIVE_MIN_INTERVAL_MS } from "./constants";

/** User-activity events that count as "the person is here" (HLD §17.5). */
const ACTIVITY_EVENTS = ["pointerdown", "keydown", "wheel", "touchstart"] as const;

/**
 * Client-side session keepalive (HLD §17.5, invariant S20: an idle tab never slides the session).
 *
 * Active only while `active` (the `authenticated` gate state). It is purely event-driven: a post
 * goes out on a user-activity event (or when the tab becomes visible again) if the page is
 * visible and at least `KEEPALIVE_MIN_INTERVAL_MS` passed since the last one. No timers, so an
 * idle tab sends nothing and the session idles out as designed. Errors are ignored: a 401 is
 * handled globally by the session-loss handler.
 */
export function useKeepalive(active: boolean, now: () => number = Date.now): void {
  useEffect(() => {
    if (!active) return undefined;
    let lastSent = Number.NEGATIVE_INFINITY;

    const maybeSend = () => {
      if (document.visibilityState !== "visible") return;
      const at = now();
      if (at - lastSent < KEEPALIVE_MIN_INTERVAL_MS) return;
      lastSent = at;
      void authApi.keepalive().catch(() => undefined);
    };
    const onVisibility = () => maybeSend(); // only sends if visible, so "becomes visible" is covered

    for (const type of ACTIVITY_EVENTS) window.addEventListener(type, maybeSend, { passive: true });
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      for (const type of ACTIVITY_EVENTS) window.removeEventListener(type, maybeSend);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [active, now]);
}
