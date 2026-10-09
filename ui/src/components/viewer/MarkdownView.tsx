import { type MouseEvent, useEffect, useMemo, useRef } from "react";
import DOMPurify from "dompurify";
import { Marked, type Tokens } from "marked";
import { api } from "../../api";
import { useTabActions } from "../../tabs/context";
import { encodeHash, makeTab } from "../../tabs/model";
import { findPathCandidates, type PathCandidate } from "../../tabs/pathLinks";
import { useResolvedPaths, useWorkspaceRoot } from "../../tabs/usePathProbe";
import { escapeHtml, highlightCode } from "./CodeView";

/**
 * Hard cap on resolved sibling images per markdown file (1B.4). Workspace markdown is
 * attacker-influenced (benchmark tiers import third-party repos) — an unbounded image list
 * would mean an unbounded number of extra `/api/files/content` round-trips per file view.
 */
export const MARKDOWN_MAX_IMAGES = 50;

// Only http(s), mailto, and relative refs — narrower than DOMPurify's own default (which
// also allows ftp/tel/callto/sms/cid/xmpp). Matches the server-side allowlist philosophy:
// markdown is rendered directly in the dashboard's own origin (unlike HtmlPreview, which is
// sandboxed), so external http(s) images/links are allowed — same as any markdown renderer
// — but nothing more exotic gets a free pass.
const ALLOWED_URI_REGEXP =
  /^(?:(?:https?|mailto):|[^a-z]|[a-z0-9+.-]+(?:[^a-z0-9+.:-]|$))/i;

const ALLOWED_TAGS = [
  "a",
  "p",
  "br",
  "hr",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "strong",
  "em",
  "del",
  "code",
  "pre",
  "blockquote",
  "ul",
  "ol",
  "li",
  "table",
  "thead",
  "tbody",
  "tr",
  "th",
  "td",
  "img",
  "span",
];

// `data-ao-pending` carries a relative image ref through sanitize without ever becoming a
// real `src` (see the renderer override below) — everything else is standard markdown output.
const ALLOWED_ATTR = [
  "href",
  "title",
  "alt",
  "src",
  "class",
  "start",
  "data-ao-pending",
];

const ABSOLUTE_SCHEME_RE = /^[a-z][a-z0-9+.-]*:/i;

/** No scheme and not protocol-relative (`//host/...`, which behaves like an absolute URL). */
function isRelativeRef(ref: string): boolean {
  return !ABSOLUTE_SCHEME_RE.test(ref) && !ref.startsWith("//");
}

function isExternalHref(href: string): boolean {
  return /^https?:\/\//i.test(href);
}

/**
 * One shared marked instance (module-scoped, built once) rather than mutating the global
 * `marked` singleton per render. Only `code` and `image` are overridden:
 *  - `code` routes fenced blocks through the exact same capped hljs path as `CodeView`
 *    (1B.4) instead of a second highlighting implementation.
 *  - `image` defers relative refs to a data attribute instead of a real `src`, so the
 *    browser never fires a doomed same-origin request before the async resolution below
 *    substitutes the real `data:` URI (or a broken-image note).
 * Everything marked emits still goes through DOMPurify afterward — this renderer only
 * decides *shape*, sanitize.ts (er, DOMPurify below) decides what's actually allowed to
 * survive into the DOM.
 */
const markdownRenderer = new Marked({
  gfm: true,
  renderer: {
    code({ text, lang }: Tokens.Code): string {
      const language =
        (lang ?? "").trim().split(/\s+/)[0]?.toLowerCase() || "plaintext";
      const { html } = highlightCode(text, language);
      return `<pre><code class="hljs">${html}</code></pre>`;
    },
    image({ href, title, text }: Tokens.Image): string {
      const alt = escapeHtml(text ?? "");
      const titleAttr = title ? ` title="${escapeHtml(title)}"` : "";
      if (isRelativeRef(href)) {
        return `<img data-ao-pending="${escapeHtml(href)}" alt="${alt}"${titleAttr}>`;
      }
      return `<img src="${escapeHtml(href)}" alt="${alt}"${titleAttr}>`;
    },
  },
});

function renderMarkdown(source: string): string {
  const html = markdownRenderer.parse(source, { async: false });
  // Mandatory even though the renderer above is already careful — DOMPurify is the actual
  // sanitizer of record (1B.1). marked passes raw inline/block HTML from the source straight
  // through by design, so a file containing `<img onerror=alert(1)>` or
  // `[x](javascript:...)` reaches this string unfiltered; only DOMPurify removes it.
  return DOMPurify.sanitize(html, {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
    ALLOWED_URI_REGEXP,
  });
}

/**
 * Joins a markdown image ref against the directory of the markdown file itself, purely to
 * build the `path` query param sent to the server. This is a plain string join, NOT a
 * security boundary and not a second path resolver — the server's `FileBrowser.resolve()` is
 * the SOLE containment authority (HLD 1B.4 / 5.3): it re-resolves the joined path against the
 * workspace root and 403/404s anything that escapes it, rendered client-side as a broken-image
 * note. Do not add `.`/`..` normalization here; that would duplicate a decision the server
 * already owns.
 */
function resolveSiblingPath(basePath: string, ref: string): string | null {
  if (ref.includes("\0")) return null;
  let decoded = ref;
  try {
    decoded = decodeURIComponent(ref);
  } catch {
    // Malformed percent-encoding — fall back to the raw ref rather than fail closed here;
    // the server will 404/403 it if it isn't a real path.
  }
  if (decoded.includes("\0") || decoded.length === 0) return null;

  const baseDir = basePath.includes("/")
    ? basePath.slice(0, basePath.lastIndexOf("/"))
    : "";
  return decoded.startsWith("/")
    ? decoded.slice(1)
    : baseDir
      ? `${baseDir}/${decoded}`
      : decoded;
}

/** Replaces a broken/unresolvable image with inert text — never a raw URL or a live link. */
function markBroken(img: HTMLImageElement) {
  const span = document.createElement("span");
  span.className = "broken-image";
  const alt = img.getAttribute("alt");
  span.textContent = alt
    ? `[image unavailable: ${alt}]`
    : "[image unavailable]";
  img.replaceWith(span);
}

/** Attribute marking an auto-linked path anchor (also the idempotence guard when re-linking). */
const PATH_LINK_ATTR = "data-ao-path";

/** Attribute on the small open-in-new-tab button built next to each auto-linked path. */
const PATH_OPEN_ATTR = "data-ao-path-open";

/** Text nodes eligible for path linking: not already inside a link or a fenced code block. */
function linkableTextNodes(container: Node): Text[] {
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  const nodes: Text[] = [];
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const parent = (n as Text).parentElement;
    if (parent?.closest(`a, pre, [${PATH_LINK_ATTR}]`)) continue;
    nodes.push(n as Text);
  }
  return nodes;
}

/**
 * Path candidates found in the (already sanitized) markdown HTML. Guesses only: the server
 * probe decides what is actually linked.
 */
function candidatesIn(html: string, root: string | null): PathCandidate[] {
  const holder = document.createElement("template");
  holder.innerHTML = html;
  const seen = new Map<string, PathCandidate>();
  for (const node of linkableTextNodes(holder.content)) {
    for (const c of findPathCandidates(node.data, root)) seen.set(c.path, c);
  }
  return [...seen.values()];
}

/**
 * `kind: "text"` markdown-extension preview (1B.2/1B.4).
 *
 * `filePath`/`root` are only used to resolve sibling image refs through the server's
 * containment-checked read endpoint — never to build a second client-side file resolver.
 */
export function MarkdownView({
  source,
  filePath,
  root,
}: {
  source: string;
  filePath: string;
  root: string;
}) {
  const html = useMemo(() => renderMarkdown(source), [source]);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const actions = useTabActions();
  const workspaceRoot = useWorkspaceRoot();
  const candidates = useMemo(
    () => candidatesIn(html, workspaceRoot),
    [html, workspaceRoot],
  );
  const statuses = useResolvedPaths(candidates.map((c) => c.path));
  const linkedKey = candidates
    .filter((c) => {
      const status = statuses.get(c.path);
      return status === "file" || status === "dir";
    })
    .map((c) => c.path)
    .join("\n");

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return undefined;
    let cancelled = false;

    const pendingImages = Array.from(
      container.querySelectorAll<HTMLImageElement>("img[data-ao-pending]"),
    );

    pendingImages.slice(0, MARKDOWN_MAX_IMAGES).forEach((img) => {
      const ref = img.getAttribute("data-ao-pending") ?? "";
      img.removeAttribute("data-ao-pending");
      const resolvedPath = resolveSiblingPath(filePath, ref);
      if (!resolvedPath) {
        markBroken(img);
        return;
      }
      api
        .readFile(resolvedPath, root)
        .then((fetched) => {
          if (cancelled) return;
          if (fetched.kind === "image" && fetched.data_uri) {
            img.src = fetched.data_uri;
          } else {
            markBroken(img);
          }
        })
        .catch(() => {
          if (cancelled) return;
          // 403 (outside root) / 404 (missing) / any other failure — all render the same
          // inert note, never the underlying path or error detail as a clickable ref.
          markBroken(img);
        });
    });

    // Past the cap: never resolved, shown the same way a failed resolution would be.
    pendingImages.slice(MARKDOWN_MAX_IMAGES).forEach((img) => {
      img.removeAttribute("data-ao-pending");
      markBroken(img);
    });

    // External links open in a new tab without handing the target page a live `window.opener`
    // back into the dashboard (reverse-tabnabbing) — applied here, not via a DOMPurify hook,
    // so it stays local to this component instead of mutating shared/global DOMPurify state.
    container.querySelectorAll<HTMLAnchorElement>("a[href]").forEach((a) => {
      const href = a.getAttribute("href") ?? "";
      if (isExternalHref(href)) {
        a.setAttribute("target", "_blank");
        a.setAttribute("rel", "noopener noreferrer");
      }
    });

    return () => {
      cancelled = true;
    };
  }, [html, filePath, root]);

  // Auto-link server-verified paths in text nodes (A5). Anchors are built with createElement +
  // textContent (never HTML strings), only for paths the probe said are file/dir, and carry a
  // hash href produced by the same `makeTab`/`encodeHash` validation as `TabLink`.
  useEffect(() => {
    const container = containerRef.current;
    if (!container || !linkedKey) return;
    const linkable = new Set(linkedKey.split("\n"));
    for (const node of linkableTextNodes(container)) {
      const found = findPathCandidates(node.data, workspaceRoot).filter((c) =>
        linkable.has(c.path),
      );
      if (found.length === 0) continue;
      const frag = document.createDocumentFragment();
      let cursor = 0;
      for (const c of found) {
        const tab = makeTab("file", { path: c.path });
        if (!tab) continue;
        if (c.start > cursor) frag.append(node.data.slice(cursor, c.start));
        const a = document.createElement("a");
        a.setAttribute("href", encodeHash(tab));
        a.setAttribute(PATH_LINK_ATTR, c.path);
        a.textContent = c.raw;
        frag.append(a);
        // Same affordance as `OpenInNewTabButton`; built with createElement (no HTML strings)
        // and handled by delegation in `onPathClick`.
        const open = document.createElement("button");
        open.setAttribute("type", "button");
        open.className = "open-in-tab";
        open.setAttribute(PATH_OPEN_ATTR, c.path);
        open.setAttribute("aria-label", `Open ${c.path} in new tab`);
        open.setAttribute("title", "Open in new tab");
        open.textContent = "⧉";
        frag.append(open);
        cursor = c.end;
      }
      frag.append(node.data.slice(cursor));
      node.replaceWith(frag);
    }
  }, [html, linkedKey, workspaceRoot]);

  // Same click semantics as `TabLink`: plain = navigate, ctrl/cmd/middle = background new tab.
  const onPathClick = (event: MouseEvent<HTMLDivElement>) => {
    const openBtn = (event.target as Element).closest?.(`button[${PATH_OPEN_ATTR}]`);
    const openPath = openBtn?.getAttribute(PATH_OPEN_ATTR);
    if (openPath && event.button === 0) {
      event.preventDefault();
      actions.open({ kind: "file", params: { path: openPath } }, { activate: true });
      return;
    }
    const anchor = (event.target as Element).closest?.(`a[${PATH_LINK_ATTR}]`);
    const path = anchor?.getAttribute(PATH_LINK_ATTR);
    if (!path || event.shiftKey || event.altKey || event.button !== 0) return;
    event.preventDefault();
    const target = { kind: "file", params: { path } } as const;
    if ((event.ctrlKey || event.metaKey) && actions.available) {
      actions.open(target, { activate: false });
    } else {
      actions.navigate(target);
    }
  };

  // Middle-click = background in-app tab (like `TabLink`'s aux handler); without this the
  // browser would open the hash URL in a new *browser* tab instead.
  const onPathAuxClick = (event: MouseEvent<HTMLDivElement>) => {
    if (event.button !== 1 || !actions.available) return;
    const anchor = (event.target as Element).closest?.(`a[${PATH_LINK_ATTR}]`);
    const path = anchor?.getAttribute(PATH_LINK_ATTR);
    if (!path) return;
    event.preventDefault();
    actions.open({ kind: "file", params: { path } }, { activate: false });
  };

  return (
    <div
      ref={containerRef}
      className="markdown-view"
      onClick={onPathClick}
      onAuxClick={onPathAuxClick}
      // Safe: `html` is DOMPurify's sanitized output (see `renderMarkdown` above), never the
      // raw markdown source or an unsanitized marked() result.
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
