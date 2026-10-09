import { useCallback, useState } from "react";

/** One localStorage key holds every section's open/closed flag as `{ [id]: boolean }`. */
export const SECTIONS_STORAGE_KEY = "ao.ui.sections.v1";
/** Allowlist for section ids: lowercase alphanumerics and dashes only. */
export const SECTION_ID_PATTERN = /^[a-z0-9-]{1,40}$/;
/** Cap on stored keys so a hostile or corrupted value cannot grow without bound. */
export const MAX_SECTION_KEYS = 64;

export type SectionState = Record<string, boolean>;

/**
 * Read the persisted section state. The stored value is untrusted: anything that is not a
 * plain object of allowlisted ids with strict boolean values is dropped, and unreadable or
 * throwing storage yields the empty state (callers then use their defaults).
 */
export function readSectionState(): SectionState {
  try {
    const raw = window.localStorage.getItem(SECTIONS_STORAGE_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    const out: SectionState = {};
    for (const [key, value] of Object.entries(parsed as Record<string, unknown>)) {
      if (Object.keys(out).length >= MAX_SECTION_KEYS) break;
      if (SECTION_ID_PATTERN.test(key) && typeof value === "boolean") out[key] = value;
    }
    return out;
  } catch {
    return {};
  }
}

/** Persist one section's flag. Invalid ids are ignored; storage failures are swallowed. */
export function writeSectionState(id: string, open: boolean): void {
  if (!SECTION_ID_PATTERN.test(id)) return;
  try {
    const next = { ...readSectionState(), [id]: open };
    window.localStorage.setItem(SECTIONS_STORAGE_KEY, JSON.stringify(next));
  } catch {
    // Quota or privacy mode: the in-memory state still works for this page view.
  }
}

/** Open/closed state for one section, persisted across remounts and reloads. */
export function useSectionOpen(id: string, defaultOpen: boolean): [boolean, () => void] {
  const [open, setOpen] = useState<boolean>(() => readSectionState()[id] ?? defaultOpen);
  const toggle = useCallback(() => {
    setOpen((prev) => {
      writeSectionState(id, !prev);
      return !prev;
    });
  }, [id]);
  return [open, toggle];
}
