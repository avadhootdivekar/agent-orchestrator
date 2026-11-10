import { useRef, type DragEvent, type KeyboardEvent, type MouseEvent } from "react";
import { hasClosableToRight, type Tab } from "./model";

/**
 * The tab strip (E-iafh2F FR-7). Closable (x button, middle-click, Delete), reorderable by
 * drag-and-drop OR Alt+Left/Right on a focused tab (keyboard parity). Roving tabindex per the
 * ARIA tabs pattern: arrows move focus, Enter/Space activate.
 *
 * Pinning: a focused tab toggles pin with `P` (or its pin button). Pinned tabs are leftmost,
 * cannot be closed (no x; Delete/middle-click ignored) until unpinned, and are skipped by
 * "Close all" / "Close to the right".
 */
export function TabBar({
  tabs,
  activeId,
  onActivate,
  onClose,
  onMove,
  onPin,
  onUnpin,
  onCloseAll,
  onCloseToRight,
}: {
  tabs: Tab[];
  activeId: string;
  onActivate: (id: string) => void;
  onClose: (id: string) => void;
  onMove: (from: number, to: number) => void;
  onPin?: (id: string) => void;
  onUnpin?: (id: string) => void;
  onCloseAll?: () => void;
  onCloseToRight?: (id: string) => void;
}) {
  const dragFrom = useRef<number | null>(null);
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  const focusTab = (index: number) => {
    const next = refs.current[Math.max(0, Math.min(tabs.length - 1, index))];
    next?.focus();
  };

  const handleKey = (event: KeyboardEvent<HTMLButtonElement>, index: number, id: string) => {
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      const delta = event.key === "ArrowLeft" ? -1 : 1;
      if (event.altKey) {
        const neighbour = tabs[index + delta];
        // A tab cannot cross the pinned boundary; leave focus where it is in that case.
        if (!neighbour || Boolean(neighbour.pinned) !== Boolean(tabs[index].pinned)) return;
        onMove(index, index + delta);
        // keep focus on the moved tab after the re-render
        setTimeout(() => focusTab(index + delta), 0);
      } else {
        focusTab(index + delta);
      }
    } else if (event.key === "Delete") {
      event.preventDefault();
      if (!tabs[index].pinned) onClose(id);
    } else if ((event.key === "p" || event.key === "P") && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault();
      togglePin(tabs[index]);
    }
  };

  const togglePin = (tab: Tab) => {
    if (tab.pinned) onUnpin?.(tab.id);
    else onPin?.(tab.id);
  };

  const handleAux = (event: MouseEvent, tab: Tab) => {
    if (event.button === 1) {
      event.preventDefault();
      if (!tab.pinned) onClose(tab.id);
    }
  };

  const onDragStart = (event: DragEvent, index: number) => {
    dragFrom.current = index;
    event.dataTransfer?.setData("text/plain", String(index)); // Firefox needs data to drag
    if (event.dataTransfer) event.dataTransfer.effectAllowed = "move";
  };
  const onDrop = (event: DragEvent, index: number) => {
    event.preventDefault();
    const from = dragFrom.current;
    dragFrom.current = null;
    if (from !== null) onMove(from, index);
  };

  const activeTab = tabs.find((t) => t.id === activeId);
  const anyClosable = tabs.some((t) => !t.pinned);

  return (
    <div className="tabbar-row">
    <div className="tabbar" role="tablist" aria-label="Open tabs">
      {tabs.map((tab, index) => {
        const active = tab.id === activeId;
        return (
          <div
            key={tab.id}
            className={`tabbar-item${active ? " is-active" : ""}${tab.pinned ? " is-pinned" : ""}`}
            draggable
            onDragStart={(e) => onDragStart(e, index)}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => onDrop(e, index)}
            onAuxClick={(e) => handleAux(e, tab)}
          >
            <button
              type="button"
              role="tab"
              id={`tab-${tab.id}`}
              aria-selected={active}
              aria-controls={`tabpanel-${tab.id}`}
              aria-keyshortcuts="Alt+ArrowLeft Alt+ArrowRight Delete P"
              tabIndex={active ? 0 : -1}
              className="tabbar-tab"
              title={`${tab.title} (Alt+←/→ to reorder, P to ${tab.pinned ? "unpin" : "pin"}${tab.pinned ? "" : ", Delete to close"})`}
              ref={(el) => {
                refs.current[index] = el;
              }}
              onClick={() => onActivate(tab.id)}
              onKeyDown={(e) => handleKey(e, index, tab.id)}
            >
              {tab.title}
            </button>
            {onPin && onUnpin ? (
              <button
                type="button"
                className="tabbar-pin"
                tabIndex={-1}
                aria-pressed={Boolean(tab.pinned)}
                aria-label={`${tab.pinned ? "Unpin" : "Pin"} tab ${tab.title}`}
                title={tab.pinned ? "Unpin tab" : "Pin tab"}
                onClick={() => togglePin(tab)}
              >
                <span aria-hidden="true">{tab.pinned ? "◆" : "◇"}</span>
              </button>
            ) : null}
            {tab.pinned ? null : (
              <button
                type="button"
                className="tabbar-close"
                tabIndex={-1}
                aria-label={`Close tab ${tab.title}`}
                onClick={() => onClose(tab.id)}
              >
                ×
              </button>
            )}
          </div>
        );
      })}
    </div>
    {onCloseToRight || onCloseAll ? (
      <div className="tabbar-actions" role="group" aria-label="Close tabs">
        {onCloseToRight ? (
          <button
            type="button"
            className="tabbar-action"
            disabled={!activeTab || !hasClosableToRight(tabs, activeTab.id)}
            title="Close the tabs to the right of the active tab (pinned tabs stay)"
            onClick={() => activeTab && onCloseToRight(activeTab.id)}
          >
            Close to the right
          </button>
        ) : null}
        {onCloseAll ? (
          <button
            type="button"
            className="tabbar-action"
            disabled={!anyClosable}
            title="Close all tabs (pinned tabs stay)"
            onClick={onCloseAll}
          >
            Close all
          </button>
        ) : null}
      </div>
    ) : null}
    </div>
  );
}
