import { LAST_LAUNCH_KEY_PREFIX } from "../launch";
import { STORAGE_KEY as TABS_STORAGE_KEY } from "../tabs/model";

/**
 * Explicit logout leaves nothing of the signed-in session's UI state behind (HLD §17.3): the open
 * tab set (`ao-tabs`, localStorage) and the remembered last launches (sessionStorage). The session
 * proof is cleared separately by `authApi.logout`. Every storage access is guarded: blocked
 * storage (private mode) must never stop a logout.
 */
export function clearClientState(): void {
  try {
    localStorage.removeItem(TABS_STORAGE_KEY);
  } catch {
    /* storage blocked: nothing persisted, nothing to clear */
  }
  try {
    // Collect first: removing while iterating shifts the indices.
    const stale: string[] = [];
    for (let i = 0; i < sessionStorage.length; i += 1) {
      const key = sessionStorage.key(i);
      if (key?.startsWith(LAST_LAUNCH_KEY_PREFIX)) stale.push(key);
    }
    for (const key of stale) sessionStorage.removeItem(key);
  } catch {
    /* storage blocked */
  }
}
