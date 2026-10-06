import { createContext, useContext } from "react";
import type { AuthStatus } from "../types";
import type { RecoveryNotice } from "./authReducer";

export interface AuthContextValue {
  /** Latest status; null when auth is off on an old backend (no `/api/auth/status`). */
  status: AuthStatus | null;
  /** Re-fetch status in place (no unmount of the dashboard). Rejects if the request fails. */
  refresh(): Promise<void>;
  /** Sign out. `everywhere` also revokes every other session. Rejects only for `everywhere` failures. */
  logout(everywhere?: boolean): Promise<void>;
  /** Set after a recovery-code sign-in until dismissed by the UI that renders it. */
  recoveryNotice: RecoveryNotice | null;
}

/** Default value = "auth off": components render fine without an `AuthGate` above them (tests). */
export const AuthContext = createContext<AuthContextValue>({
  status: null,
  refresh: async () => {},
  logout: async () => {},
  recoveryNotice: null,
});

export function useAuth(): AuthContextValue {
  return useContext(AuthContext);
}
