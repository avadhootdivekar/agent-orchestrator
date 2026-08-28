import type { CSSProperties } from "react";

/** Persisted under one namespaced localStorage key (1B.6), mirroring App.tsx's THEME_KEY. */
export const TYPOGRAPHY_STORAGE_KEY = "ao-viewer-typography";

export type FontFamily = "system" | "serif" | "mono";

export interface Typography {
  family: FontFamily;
  size: number;
  lineHeight: number;
  letterSpacing: number;
}

export const DEFAULT_TYPOGRAPHY: Typography = {
  family: "system",
  size: 13,
  lineHeight: 1.6,
  letterSpacing: 0,
};

/** Range constants for each numeric field — also the clamp bounds for stored values. */
const LIMITS = {
  size: { min: 11, max: 22 },
  lineHeight: { min: 1.1, max: 2.4 },
  letterSpacing: { min: -0.02, max: 0.08 },
} as const;

const FONT_STACKS: Record<FontFamily, string> = {
  system: "var(--font-sans)",
  serif: 'Georgia, Cambria, "Times New Roman", Times, serif',
  mono: "var(--font-mono)",
};

function clampNumber(value: unknown, fallback: number, min: number, max: number): number {
  // `Number(null)` is 0 (finite!) and `Number("")` is 0 too — both would otherwise silently
  // clamp to `min` instead of falling back. Treat anything that isn't already a genuine
  // number or a non-empty numeric string as missing.
  if (typeof value !== "number" && typeof value !== "string") return fallback;
  if (typeof value === "string" && value.trim() === "") return fallback;
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(max, Math.max(min, n));
}

/**
 * Validates/clamps a value read back from localStorage field by field, never trusting the
 * stored shape wholesale — a corrupted or hand-edited entry falls back per-field to the
 * default rather than being rejected outright.
 */
export function clampTypography(raw: unknown): Typography {
  const source = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  const family: FontFamily =
    source.family === "serif" || source.family === "mono" || source.family === "system"
      ? source.family
      : DEFAULT_TYPOGRAPHY.family;
  return {
    family,
    size: clampNumber(source.size, DEFAULT_TYPOGRAPHY.size, LIMITS.size.min, LIMITS.size.max),
    lineHeight: clampNumber(
      source.lineHeight,
      DEFAULT_TYPOGRAPHY.lineHeight,
      LIMITS.lineHeight.min,
      LIMITS.lineHeight.max,
    ),
    letterSpacing: clampNumber(
      source.letterSpacing,
      DEFAULT_TYPOGRAPHY.letterSpacing,
      LIMITS.letterSpacing.min,
      LIMITS.letterSpacing.max,
    ),
  };
}

export function loadTypography(): Typography {
  const raw = localStorage.getItem(TYPOGRAPHY_STORAGE_KEY);
  if (!raw) return DEFAULT_TYPOGRAPHY;
  try {
    return clampTypography(JSON.parse(raw));
  } catch {
    // Malformed JSON (hand-edited or from a future/older version) — never let a bad
    // localStorage value crash the viewer, just fall back to defaults.
    return DEFAULT_TYPOGRAPHY;
  }
}

export function saveTypography(value: Typography): void {
  localStorage.setItem(TYPOGRAPHY_STORAGE_KEY, JSON.stringify(value));
}

/**
 * CSS custom properties for the viewer container. Values are clamped `Typography` fields
 * only — never a raw string pulled straight from storage or user input — so this is safe to
 * spread directly into a `style` prop.
 */
export function typographyCssVars(value: Typography): CSSProperties {
  return {
    "--viewer-font-family": FONT_STACKS[value.family],
    "--viewer-font-size": `${value.size}px`,
    "--viewer-line-height": String(value.lineHeight),
    "--viewer-letter-spacing": `${value.letterSpacing}em`,
  } as CSSProperties;
}

/**
 * Font family / size / line height / letter spacing controls (1B.6).
 *
 * Applies to CodeView, MarkdownView, and plain text via `typographyCssVars` on their shared
 * wrapper. Deliberately NOT applied to HtmlPreview — that content brings its own styling, and
 * overriding it would misrepresent what the sanitized document actually looks like.
 */
export function TypographyControls({
  value,
  onChange,
}: {
  value: Typography;
  onChange: (next: Typography) => void;
}) {
  return (
    <div className="typography-controls row" aria-label="Viewer typography">
      <label className="field-inline">
        <span>Font</span>
        <select
          value={value.family}
          onChange={(event) =>
            onChange({ ...value, family: event.target.value as FontFamily })
          }
        >
          <option value="system">System</option>
          <option value="serif">Serif</option>
          <option value="mono">Mono</option>
        </select>
      </label>

      <label className="field-inline">
        <span>Size</span>
        <input
          type="range"
          min={LIMITS.size.min}
          max={LIMITS.size.max}
          step={1}
          value={value.size}
          onChange={(event) => onChange({ ...value, size: Number(event.target.value) })}
        />
        <span className="muted mono">{value.size}px</span>
      </label>

      <label className="field-inline">
        <span>Line height</span>
        <input
          type="range"
          min={LIMITS.lineHeight.min}
          max={LIMITS.lineHeight.max}
          step={0.1}
          value={value.lineHeight}
          onChange={(event) => onChange({ ...value, lineHeight: Number(event.target.value) })}
        />
        <span className="muted mono">{value.lineHeight.toFixed(1)}</span>
      </label>

      <label className="field-inline">
        <span>Letter spacing</span>
        <input
          type="range"
          min={LIMITS.letterSpacing.min}
          max={LIMITS.letterSpacing.max}
          step={0.01}
          value={value.letterSpacing}
          onChange={(event) =>
            onChange({ ...value, letterSpacing: Number(event.target.value) })
          }
        />
        <span className="muted mono">{value.letterSpacing.toFixed(2)}em</span>
      </label>
    </div>
  );
}
