import { createContext, useContext } from "react";
import type { TabKind, TabParams } from "./model";

/**
 * Whether the tab a component lives in is the visible one. Inactive tabs stay MOUNTED (their
 * state survives) but `usePolling` reads this and stops making requests (E-iafh2F FR-10).
 * Default `true`: a component rendered outside the workspace (tests) polls as before.
 */
export const TabActiveContext = createContext<boolean>(true);
export const useTabActive = (): boolean => useContext(TabActiveContext);

export interface TabTarget {
  kind: TabKind;
  params: TabParams;
}

export interface TabActions {
  /** False outside the workspace shell: contextual "open in new tab" affordances hide. */
  available: boolean;
  /** Plain navigation: reuse an identical tab, else replace the active tab's content. */
  navigate: (target: TabTarget) => void;
  /** New tab (deduplicated by target). `activate` switches to it; ctrl/middle-click does not. */
  open: (target: TabTarget, opts?: { activate?: boolean }) => void;
  /**
   * Update tab `tabId`'s own target in place (params + derived title + URL hash) without focusing
   * or creating anything. For views that change what they show internally (FileBrowser).
   */
  retarget: (tabId: string, target: TabTarget) => void;
}

const NOOP_ACTIONS: TabActions = {
  available: false,
  navigate: () => {},
  open: () => {},
  retarget: () => {},
};

export const TabActionsContext = createContext<TabActions>(NOOP_ACTIONS);
export const useTabActions = (): TabActions => useContext(TabActionsContext);
