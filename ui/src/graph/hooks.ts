/**
 * Shared canvas interaction hooks (`T-aHktGB`, HLD §8.6 item 4 / §8.7).
 *
 * Extracted so both this task's search-to-focus toolbar and `T-pAi0Cv`'s hover-card/detail-panel
 * links reuse the exact same "jump to a node" behavior instead of re-deriving it. Pure React
 * (no fetch, no localStorage) -- unlike `./model.ts`/`./layout.ts` this file DOES depend on
 * `@xyflow/react` (`useReactFlow`), so it stays out of those two purity-tested modules.
 */
import { useCallback, useEffect, useRef } from "react";
import { useReactFlow } from "@xyflow/react";
import type { LayoutResult } from "../types";
import { NODE_HEIGHT, NODE_WIDTH } from "./model";

/**
 * Zoom floor applied whenever `selectAndCenter` recenters the viewport (HLD §8.6 item 4: "Enter
 * centers the first match ... with a zoom of at least 1"). The current zoom is kept when it is
 * already above this floor, so zooming in further than 1x before selecting isn't undone.
 */
export const SELECT_CENTER_MIN_ZOOM = 1;

export interface UseSelectAndCenterOptions {
  /** Latest layout snapshot; `null` before the first layout resolves. */
  layout: LayoutResult | null;
  /** True when `id` is currently filtered out by the "show unrelated" toggle. */
  isHidden: (id: string) => boolean;
  /** Turns the unrelated filter on so a hidden target becomes visible (HLD §8.7). */
  setShowUnrelated: (value: boolean) => void;
  /** Marks `id` selected (drives `TaskNode`'s `selected` styling / a future detail panel). */
  onSelect: (id: string) => void;
}

/**
 * Returns `selectAndCenter(id)`: the one behavior every navigable link in this feature uses
 * (search results here, and parent/child/dependency links in `T-pAi0Cv`'s detail panel).
 *
 * If `id` is hidden by the "show unrelated" filter, the filter is switched on and the center is
 * *deferred* until the relayout that follows actually places `id` (dagre is async, so the
 * position isn't available the same tick) -- otherwise it centers immediately.
 */
export function useSelectAndCenter({
  layout,
  isHidden,
  setShowUnrelated,
  onSelect,
}: UseSelectAndCenterOptions): (id: string) => void {
  const reactFlow = useReactFlow();
  // Set while waiting for a relayout triggered by auto-enabling the unrelated filter.
  const pendingIdRef = useRef<string | null>(null);

  const centerOn = useCallback(
    (id: string, currentLayout: LayoutResult) => {
      const position = currentLayout.positions.get(id);
      if (!position) return;
      void reactFlow.setCenter(position.x + NODE_WIDTH / 2, position.y + NODE_HEIGHT / 2, {
        zoom: Math.max(reactFlow.getZoom(), SELECT_CENTER_MIN_ZOOM),
      });
      onSelect(id);
    },
    [reactFlow, onSelect],
  );

  // Finishes a deferred center once the relayout it waited for lands (a fresh `layout` that now
  // actually places the previously-hidden node).
  useEffect(() => {
    const pendingId = pendingIdRef.current;
    if (!pendingId || !layout || !layout.positions.has(pendingId)) return;
    pendingIdRef.current = null;
    centerOn(pendingId, layout);
  }, [layout, centerOn]);

  return useCallback(
    (id: string) => {
      if (isHidden(id)) {
        pendingIdRef.current = id;
        setShowUnrelated(true);
        return;
      }
      if (!layout) return;
      centerOn(id, layout);
    },
    [isHidden, setShowUnrelated, layout, centerOn],
  );
}
