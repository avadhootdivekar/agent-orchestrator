import type { MouseEvent, ReactNode } from "react";
import { useTabActions, type TabTarget } from "./context";
import { encodeHash, makeTab } from "./model";

/**
 * A link to a workspace tab (E-iafh2F FR-8). Renders a REAL anchor whose `href` is the tab's
 * hash URL, so right-click / shift-click / "copy link address" and opening it in a new
 * browser tab all work natively (the hash restores the tab on load).
 *
 *  - plain click            -> navigate within the current tab (`onPlainClick` when given)
 *  - ctrl/cmd or middle     -> open in a NEW in-app tab, in the background
 *  - invalid target         -> renders plain text (never a link built from bad params)
 */
export function TabLink({
  target,
  children,
  className,
  onPlainClick,
  title,
}: {
  target: TabTarget;
  children: ReactNode;
  className?: string;
  onPlainClick?: () => void;
  title?: string;
}) {
  const actions = useTabActions();
  const tab = makeTab(target.kind, target.params);
  if (!tab) return <span className={className}>{children}</span>;

  const plain = () => (onPlainClick ? onPlainClick() : actions.navigate(target));
  const handleClick = (event: MouseEvent<HTMLAnchorElement>) => {
    if (event.shiftKey || event.altKey || event.button !== 0) return; // native behaviour
    event.preventDefault();
    if ((event.ctrlKey || event.metaKey) && actions.available) {
      actions.open(target, { activate: false });
    } else {
      plain();
    }
  };
  const handleAux = (event: MouseEvent<HTMLAnchorElement>) => {
    if (event.button !== 1) return;
    event.preventDefault();
    if (actions.available) actions.open(target, { activate: false });
    else plain();
  };

  return (
    <a
      href={encodeHash(tab)}
      className={className}
      title={title}
      onClick={handleClick}
      onAuxClick={handleAux}
    >
      {children}
    </a>
  );
}

/**
 * Explicit "open in new tab" action (FR-8): opens the target in a new in-app tab and switches
 * to it. Hidden outside the workspace shell and for an invalid target.
 */
export function OpenInNewTabButton({ target, label }: { target: TabTarget; label: string }) {
  const actions = useTabActions();
  if (!actions.available || !makeTab(target.kind, target.params)) return null;
  return (
    <button
      type="button"
      className="open-in-tab"
      aria-label={`Open ${label} in new tab`}
      title="Open in new tab"
      onClick={(event) => {
        event.stopPropagation();
        actions.open(target, { activate: true });
      }}
    >
      <span aria-hidden="true">⧉</span>
    </button>
  );
}
