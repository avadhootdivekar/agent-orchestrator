/**
 * Launch-result helpers (E-iafh2F launch-status): pure status classification plus the guarded
 * browser-storage bits that let a failed launch outlive navigation. No React here.
 */

import type { LaunchRecord, LaunchStatus } from "./types";

/** How often the launch panel re-reads the launch record while the run id is not visible. */
export const LAUNCH_POLL_MS = 1500;
/** Polls before the panel stops and says "not confirmed yet" (~30 s). Keep waiting resets it. */
export const LAUNCH_MAX_POLLS = 20;
/** Failed-to-start launches stay on the runs list this long (or until dismissed). */
export const FAILED_LAUNCH_WINDOW_HOURS = 24;

/** sessionStorage key prefix of the remembered last launch (cleared on explicit logout). */
export const LAST_LAUNCH_KEY_PREFIX = "ao.launch.last.";
const DISMISSED_KEY = "ao.launch.dismissed";
/** Bound on remembered dismissals so the key can never grow without limit. */
const MAX_DISMISSED = 200;

/**
 * The server's derived status, with a fallback for a backend that predates the field: a run
 * id means started; an exit means the process died before one appeared; otherwise unknown
 * and still starting.
 */
export function launchStatusOf(launch: LaunchRecord): LaunchStatus {
  if (launch.status) return launch.status;
  if (launch.run_id) return "started";
  if (launch.exit_code !== null || launch.finished_at) return "failed_to_start";
  return "starting";
}

/** True while the process is alive but no run directory has been attributed yet. */
export function isWaiting(status: LaunchStatus): boolean {
  return status === "starting" || status === "running_unconfirmed";
}

export function readStoredLaunchId(scope: string): string | null {
  try {
    return window.sessionStorage.getItem(LAST_LAUNCH_KEY_PREFIX + scope);
  } catch {
    return null;
  }
}

export function writeStoredLaunchId(
  scope: string,
  launchId: string | null,
): void {
  try {
    if (launchId)
      window.sessionStorage.setItem(LAST_LAUNCH_KEY_PREFIX + scope, launchId);
    else window.sessionStorage.removeItem(LAST_LAUNCH_KEY_PREFIX + scope);
  } catch {
    /* storage unavailable: the panel still works for this page view */
  }
}

export function readDismissedLaunches(): string[] {
  try {
    const parsed: unknown = JSON.parse(
      window.localStorage.getItem(DISMISSED_KEY) ?? "[]",
    );
    return Array.isArray(parsed)
      ? parsed.filter((v): v is string => typeof v === "string")
      : [];
  } catch {
    return [];
  }
}

/** Remember a dismissal (newest kept when the bound is hit). Returns the updated list. */
export function dismissLaunch(launchId: string): string[] {
  const next = [
    ...readDismissedLaunches().filter((id) => id !== launchId),
    launchId,
  ].slice(-MAX_DISMISSED);
  try {
    window.localStorage.setItem(DISMISSED_KEY, JSON.stringify(next));
  } catch {
    /* storage unavailable: dismissed for this page view only */
  }
  return next;
}
