import { PathLink } from "./PathText";

/** One quick link: a workspace path (relative or absolute) and an optional label. */
export interface QuickLink {
  path: string;
  label?: string;
}

/** Upper bound on listed links; a run with more outputs shows the first ones only. */
export const QUICK_LINKS_MAX = 30;

/** Distinct, non-empty paths in first-seen order, capped at {@link QUICK_LINKS_MAX}. */
function dedupe(links: readonly QuickLink[]): QuickLink[] {
  const seen = new Set<string>();
  const out: QuickLink[] = [];
  for (const link of links) {
    if (!link.path || seen.has(link.path)) continue;
    seen.add(link.path);
    out.push(link);
    if (out.length >= QUICK_LINKS_MAX) break;
  }
  return out;
}

/**
 * A run's key files (workflow.json, prompt, outputs) as links to the file viewer. Each entry is
 * a `PathLink`, so it is only a link once the server confirms the path exists inside the
 * workspace; otherwise it shows as plain text. Purely presentational: the caller supplies the
 * paths and decides where to mount it. Renders nothing when there is nothing to list.
 */
export function QuickLinks({
  links,
  title = "Quick links",
}: {
  links: readonly QuickLink[];
  title?: string;
}) {
  const items = dedupe(links);
  if (items.length === 0) return null;
  return (
    <nav className="quick-links" aria-label={title}>
      <span className="muted">{title}</span>
      <ul
        className="row"
        style={{
          gap: 12,
          flexWrap: "wrap",
          listStyle: "none",
          margin: 0,
          padding: 0,
        }}
      >
        {items.map(({ path, label }) => (
          <li key={path} className="mono">
            <PathLink path={path}>{label ?? path}</PathLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
