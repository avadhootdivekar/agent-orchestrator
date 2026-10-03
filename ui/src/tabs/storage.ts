import { parsePersisted, serialize, STORAGE_KEY, type TabsState } from "./model";

/** Guarded localStorage read: private windows / blocked storage / quota just return `null`. */
export function readStoredTabs(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

/** Guarded write; failure is silent (the workspace works without persistence). */
export function writeStoredTabs(state: TabsState): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, serialize(state));
  } catch {
    /* storage unavailable: nothing to do */
  }
}

export { parsePersisted };
