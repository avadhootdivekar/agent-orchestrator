import { ApiError } from "./api";

/** Shown when the request never reached the server (backend down, restarting, offline). */
export const NETWORK_ERROR_MESSAGE = "Can't reach the dashboard server — retrying…";

/**
 * User-facing text for a failed API call. Server errors keep their own message; a
 * network-level failure (fetch rejects with a TypeError such as "Failed to fetch") becomes a
 * plain-language hint instead of a raw exception string.
 */
export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof TypeError) return NETWORK_ERROR_MESSAGE;
  return String(err);
}
