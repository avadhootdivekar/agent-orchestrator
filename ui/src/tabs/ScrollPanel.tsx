import { useLayoutEffect, useRef, type ReactNode } from "react";

/**
 * A tab's own scroll container. Tabs stay mounted but inactive ones are `hidden`
 * (display:none), and browsers discard scroll offsets of display:none boxes -- so the offset is
 * remembered on scroll and restored when the panel becomes visible again.
 */
export function ScrollPanel({
  id,
  labelledBy,
  hidden,
  children,
}: {
  id: string;
  labelledBy: string;
  hidden: boolean;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const saved = useRef(0);

  useLayoutEffect(() => {
    if (!hidden && ref.current) ref.current.scrollTop = saved.current;
  }, [hidden]);

  return (
    <div
      ref={ref}
      id={id}
      role="tabpanel"
      aria-labelledby={labelledBy}
      className="tab-panel"
      hidden={hidden}
      onScroll={(e) => {
        // A hidden panel can fire a reset scroll event; only record while visible.
        if (!hidden) saved.current = e.currentTarget.scrollTop;
      }}
    >
      {children}
    </div>
  );
}
