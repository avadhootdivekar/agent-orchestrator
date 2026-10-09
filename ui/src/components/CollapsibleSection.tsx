import { useEffect, useState, type ReactNode } from "react";
import { useSectionOpen } from "../sections";

/**
 * A titled run-page section that collapses. State is persisted per section id (see
 * `sections.ts`). The toggle is a real `<button>` (Enter/Space for free) with `aria-expanded`;
 * `actions` sit outside it so interactive controls never nest. With `lazy`, children mount on
 * first open and then stay mounted.
 */
export function CollapsibleSection({
  id,
  title,
  defaultOpen = true,
  actions,
  lazy = false,
  className,
  testId,
  children,
}: {
  id: string;
  title: ReactNode;
  defaultOpen?: boolean;
  actions?: ReactNode;
  lazy?: boolean;
  className?: string;
  testId?: string;
  children: ReactNode;
}) {
  const [open, toggle] = useSectionOpen(id, defaultOpen);
  const [everOpened, setEverOpened] = useState(open);
  useEffect(() => {
    if (open) setEverOpened(true);
  }, [open]);
  const bodyId = `sec-${id}`;
  const headId = `sec-head-${id}`;
  return (
    <section
      className={["card", "section", className].filter(Boolean).join(" ")}
      data-testid={testId ?? `section-${id}`}
    >
      <h2 className="section-head" id={headId}>
        <button type="button" className="section-toggle" aria-expanded={open} aria-controls={bodyId} onClick={toggle}>
          <span aria-hidden="true" className="section-caret">
            {open ? "▾" : "▸"}
          </span>
          {title}
        </button>
        {actions ? <span className="section-actions">{actions}</span> : null}
      </h2>
      <div id={bodyId} role="region" aria-labelledby={headId} hidden={!open} className="section-body">
        {!lazy || everOpened ? children : null}
      </div>
    </section>
  );
}
