import { Fragment, type ReactNode } from "react";
import { OpenInNewTabButton, TabLink } from "../tabs/TabLink";
import { findPathCandidates, normalizePath } from "../tabs/pathLinks";
import { useResolvedPaths, useWorkspaceRoot } from "../tabs/usePathProbe";
import type { PathProbeStatus } from "../types";

const LINKABLE: ReadonlySet<PathProbeStatus> = new Set(["file", "dir"]);

function PathAnchor({ path, children }: { path: string; children: ReactNode }) {
  const target = { kind: "file", params: { path } } as const;
  return (
    <>
      <TabLink target={target}>{children}</TabLink>
      <OpenInNewTabButton target={target} label={path} />
    </>
  );
}

/**
 * Renders *children* (a known path, e.g. a declared task output) as a file-viewer link only
 * after the server confirms it exists inside the workspace; plain text while pending, missing,
 * denied, out-of-root or on error (A5).
 */
export function PathLink({ path, children }: { path: string; children?: ReactNode }) {
  const root = useWorkspaceRoot();
  const rel = normalizePath(path, root);
  const statuses = useResolvedPaths(rel ? [rel] : []);
  if (!rel || !LINKABLE.has(statuses.get(rel) as PathProbeStatus)) return <>{children ?? path}</>;
  return <PathAnchor path={rel}>{children ?? path}</PathAnchor>;
}

/**
 * Free text with auto-linked file paths. Candidates are guessed client-side but only linked
 * when `POST /api/files/resolve` says `file`/`dir`; everything else stays literal text. React
 * text nodes only -- never `dangerouslySetInnerHTML`.
 */
export function PathText({ text }: { text: string }) {
  const root = useWorkspaceRoot();
  const candidates = findPathCandidates(text, root);
  const statuses = useResolvedPaths(candidates.map((c) => c.path));
  const parts: ReactNode[] = [];
  let cursor = 0;
  candidates.forEach((c, i) => {
    if (!LINKABLE.has(statuses.get(c.path) as PathProbeStatus)) return;
    if (c.start > cursor) parts.push(text.slice(cursor, c.start));
    parts.push(
      <PathAnchor key={`${c.start}-${i}`} path={c.path}>
        {c.raw}
      </PathAnchor>,
    );
    cursor = c.end;
  });
  parts.push(text.slice(cursor));
  return (
    <>
      {parts.map((part, i) => (
        <Fragment key={i}>{part}</Fragment>
      ))}
    </>
  );
}
