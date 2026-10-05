import { useId, type ReactNode } from "react";

/**
 * Modal shell for the account dialogs. No Escape/backdrop dismissal on purpose: a stray key press
 * must never throw away recovery codes that are shown exactly once. Every dialog has its own
 * explicit Cancel/Close button.
 */
export function AuthDialog({ title, children }: { title: string; children: ReactNode }) {
  const titleId = useId();
  return (
    <div className="dialog-backdrop">
      <div role="dialog" aria-modal="true" aria-labelledby={titleId} className="dialog auth-card">
        <h1 id={titleId}>{title}</h1>
        {children}
      </div>
    </div>
  );
}
