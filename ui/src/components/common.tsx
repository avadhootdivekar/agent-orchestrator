import type { ReactNode } from "react";
import { statusGlyph, statusTone } from "../format";

/**
 * Status chip: glyph + word + color.
 *
 * The glyph and the word are the primary channel; color reinforces. This is the status
 * palette's "never color alone" rule, and it is also what makes the dashboard readable
 * in grayscale print and under forced-colors.
 */
export function StatusChip({ status }: { status: string }) {
  return (
    <span className={`chip ${statusTone(status)}`}>
      <span className="chip-glyph" aria-hidden="true">
        {statusGlyph(status)}
      </span>
      {status}
    </span>
  );
}

/** Small labelled metric block. `value` uses proportional figures by design. */
export function Tile({
  label,
  value,
  hint,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
}) {
  return (
    <div className="tile">
      <div className="tile-label">{label}</div>
      <div className="tile-value">{value}</div>
      {hint ? <div className="tile-hint">{hint}</div> : null}
    </div>
  );
}

export function ErrorBanner({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <div className="banner error" role="alert">
      {message}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

/** Marks a run whose process is currently alive. */
export function LiveBadge() {
  return (
    <span className="chip active" title="A process for this run is running now">
      <span className="live-dot" aria-hidden="true" />
      live
    </span>
  );
}
