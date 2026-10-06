/**
 * Session-proof storage (D25 / ADR-0021 D11).
 *
 * The proof is read from `localStorage` on EVERY call so that a rotation made in another tab is
 * picked up immediately (R15). Every storage access is guarded: when storage throws (private
 * mode, blocked site data) the proof lives in memory for this page only and
 * `proofStorageBlocked()` turns true so the login screen can say so. The proof is never logged,
 * never placed in a URL and never rendered.
 */
import { PROOF_STORAGE_KEY } from "./constants";

let memoryProof: string | null = null;
let storageBlocked = false;

export function readProof(): string | null {
  try {
    const stored = localStorage.getItem(PROOF_STORAGE_KEY);
    // A stored value wins (shared across tabs); otherwise fall back to this page's memory copy.
    return stored ?? memoryProof;
  } catch {
    storageBlocked = true;
    return memoryProof;
  }
}

export function writeProof(proof: string): void {
  memoryProof = proof;
  try {
    localStorage.setItem(PROOF_STORAGE_KEY, proof);
  } catch {
    storageBlocked = true;
  }
}

export function clearProof(): void {
  memoryProof = null;
  try {
    localStorage.removeItem(PROOF_STORAGE_KEY);
  } catch {
    storageBlocked = true;
  }
}

/** True once any storage access has thrown in this page. */
export function proofStorageBlocked(): boolean {
  return storageBlocked;
}

/** Test seam: reset module state between tests. */
export function resetProofStateForTests(): void {
  memoryProof = null;
  storageBlocked = false;
}
