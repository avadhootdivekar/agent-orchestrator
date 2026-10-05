/** Named constants for the dashboard auth UI (HLD §12.6, §17.8). Strings are verbatim from §17.8. */

/** localStorage key holding the session proof (D25). Shared by every tab of this origin. */
export const PROOF_STORAGE_KEY = "ao-session-proof";

/** Minimum gap between keepalive posts (used by T-vCgsU6's `useKeepalive`). */
export const KEEPALIVE_MIN_INTERVAL_MS = 60_000;

/** Countdown tick for the 429 "try again in n seconds" notice. */
export const COUNTDOWN_TICK_MS = 1000;

/** Length of a TOTP code (digits). */
export const TOTP_CODE_LENGTH = 6;

export const MSG_INVALID_CREDENTIALS = "Invalid username or password.";
export const MSG_SERVER_BUSY = "The server is busy. Please try again in a moment.";
export const MSG_TRANSPORT_BANNER =
  "This connection is not encrypted. Your password and codes can be read on the network.";
export const MSG_SESSION_TIMED_OUT = "Your sign-in timed out. Please sign in again.";
export const MSG_SIGNED_OUT = "You have been signed out.";
export const MSG_TOTP_REQUIRED_POLICY_OFF =
  "Your account requires two-factor authentication, but enrollment is disabled on this server. Ask the operator.";
export const MSG_REPLAYED_CODE =
  "This code was already used (codes work once, across the hub and every dashboard). Wait for the next code from your authenticator app.";
export const MSG_FORCED_ENROLL_INTRO =
  "Two-factor authentication is required for your account. Scan the QR code (or enter the setup key) in your authenticator app, then enter the 6-digit code.";
export const MSG_STORAGE_BLOCKED =
  "This browser is blocking site storage, so you will need to sign in again after reloading the page.";

export function msgTooManyAttempts(seconds: number): string {
  return `Too many attempts. Try again in ${seconds} seconds.`;
}

export function msgInvalidCode(attemptsRemaining: number | undefined): string {
  const base =
    "That code didn't work. Check the time on this computer and your phone, then try the current code.";
  return attemptsRemaining === undefined ? base : `${base} (${attemptsRemaining} attempts left)`;
}

export function msgRecoveryUsed(remaining: number): string {
  return `You signed in with a recovery code. ${remaining} codes left. Set up a new authenticator under Account → Two-factor.`;
}
