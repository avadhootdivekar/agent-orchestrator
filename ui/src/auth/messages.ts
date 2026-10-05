import { ApiError } from "../api";
import {
  MSG_INSECURE_TRANSPORT,
  MSG_INVALID_CREDENTIALS,
  MSG_REPLAYED_CODE,
  MSG_SERVER_BUSY,
  MSG_TOTP_REQUIRED_POLICY_OFF,
  msgInvalidCode,
  msgTooManyAttempts,
} from "./constants";

const MSG_NETWORK = "Could not reach the server. Please try again.";

function attemptsRemaining(error: ApiError): number | undefined {
  const value = error.extra?.attempts_remaining;
  return typeof value === "number" ? value : undefined;
}

/** Map a failed auth call to the exact HLD §17.8 string (falling back to the server's `detail`). */
export function authErrorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return MSG_NETWORK;
  switch (error.code) {
    case "invalid_credentials":
      return MSG_INVALID_CREDENTIALS;
    case "too_many_attempts":
      return msgTooManyAttempts(error.retryAfterSeconds ?? 0);
    case "invalid_code":
      // §17.8: the replayed string is shown exactly (the hub page does the same): no attempts suffix.
      return error.extra?.reason === "replayed"
        ? MSG_REPLAYED_CODE
        : msgInvalidCode(attemptsRemaining(error));
    case "busy":
    case "store_unavailable":
      return MSG_SERVER_BUSY;
    case "totp_required":
      return MSG_TOTP_REQUIRED_POLICY_OFF;
    case "insecure_transport":
      return MSG_INSECURE_TRANSPORT;
    default:
      return error.message || MSG_NETWORK;
  }
}

/**
 * Like `authErrorMessage`, for the signed-in account dialogs. The one difference: a 403
 * `totp_required` there means "you may not disable 2FA", whose reason is the server's `detail`
 * (the login-time string is about a different situation).
 */
export function accountErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.code === "totp_required") return error.message;
  return authErrorMessage(error);
}

/** Seconds to lock the form for, when the error is a 429 / 503-with-Retry-After; else 0. */
export function lockoutSeconds(error: unknown): number {
  if (!(error instanceof ApiError)) return 0;
  return error.status === 429 ? Math.ceil(error.retryAfterSeconds ?? 0) : 0;
}
