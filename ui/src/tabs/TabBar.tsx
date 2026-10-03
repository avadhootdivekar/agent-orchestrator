import { useRef, type DragEvent, type KeyboardEvent, type MouseEvent } from "react";
import type { Tab } from "./model";

/**
 * The tab strip (E-iafh2F FR-7). Closable (x button, middle-click, Delete), reorderable by
 * drag-and-drop OR Alt+Left/Right on a focused tab (keyboard parity). Roving tabindex per the
 * ARIA tabs pattern: arrows move focus, Enter/Space activate.
 */
export function TabBar({
  tabs,
  activeId,
  onActivate,
  onClose,
  onMove,
}: {
  tabs: Tab[];
  activeId: string;
  onActivate: (id: string) => void;
  onClose: (id: string) => void;
  onMove: (from: number, to: number) => void;
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
        onMove(index, index + delta);
        // keep focus on the moved tab after the re-render
        setTimeout(() => focusTab(index + delta), 0);
      } else {
        focusTab(index + delta);
      }
    } else if (event.key === "Delete") {
      event.preventDefault();
      onClose(id);
    }
  };

  const handleAux = (event: MouseEvent, id: string) => {
    if (event.button === 1) {
      event.preventDefault();
      onClose(id);
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

  return (
    <div className="tabbar" role="tablist" aria-label="Open tabs">
      {tabs.map((tab, index) => {
        const active = tab.id === activeId;
        return (
          <div
            key={tab.id}
            className={`tabbar-item${active ? " is-active" : ""}`}
            draggable
            onDragStart={(e) => onDragStart(e, index)}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => onDrop(e, index)}
            onAuxClick={(e) => handleAux(e, tab.id)}
          >
            <button
              type="button"
              role="tab"
              id={`tab-${tab.id}`}
              aria-selected={active}
              aria-controls={`tabpanel-${tab.id}`}
              aria-keyshortcuts="Alt+ArrowLeft Alt+ArrowRight Delete"
              tabIndex={active ? 0 : -1}
              className="tabbar-tab"
              title={`${tab.title} (Alt+←/→ to reorder, Delete to close)`}
              ref={(el) => {
                refs.current[index] = el;
              }}
              onClick={() => onActivate(tab.id)}
              onKeyDown={(e) => handleKey(e, index, tab.id)}
            >
              {tab.title}
            </button>
            <button
              type="button"
              className="tabbar-close"
              aria-label={`Close tab ${tab.title}`}
              onClick={() => onClose(tab.id)}
            >
              ×
            </button>
          </div>
        );
      })}
    </div>
  );
}
