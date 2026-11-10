import { useMemo, type MouseEvent } from "react";
import hljs from "highlight.js/lib/core";
import bash from "highlight.js/lib/languages/bash";
import c from "highlight.js/lib/languages/c";
import cpp from "highlight.js/lib/languages/cpp";
import css from "highlight.js/lib/languages/css";
import diff from "highlight.js/lib/languages/diff";
import dockerfile from "highlight.js/lib/languages/dockerfile";
import go from "highlight.js/lib/languages/go";
import ini from "highlight.js/lib/languages/ini";
import java from "highlight.js/lib/languages/java";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import markdown from "highlight.js/lib/languages/markdown";
import python from "highlight.js/lib/languages/python";
import rust from "highlight.js/lib/languages/rust";
import sql from "highlight.js/lib/languages/sql";
import typescript from "highlight.js/lib/languages/typescript";
import xml from "highlight.js/lib/languages/xml";
import yaml from "highlight.js/lib/languages/yaml";
import { formatBytes } from "../../format";
import { useTabActions } from "../../tabs/context";
import { encodeHash, makeTab } from "../../tabs/model";
import { MAX_CANDIDATES_PER_TEXT, findPathCandidates } from "../../tabs/pathLinks";
import { useResolvedPaths, useWorkspaceRoot } from "../../tabs/usePathProbe";

/**
 * Curated highlight.js language subset (1B.1).
 *
 * We import `highlight.js/lib/core` (no bundled grammars) and register only these — the
 * highlighter ships inside the Python wheel, so pulling in the full ~190-language bundle
 * would be a real, permanent size cost for languages this project never uses.
 *
 * `ini.js` registers the `toml` alias itself, and `xml.js` registers `html`/`svg`/`xhtml`
 * aliases itself — both covered without a separate import.
 */
hljs.registerLanguage("python", python);
hljs.registerLanguage("typescript", typescript);
hljs.registerLanguage("javascript", javascript);
hljs.registerLanguage("json", json);
hljs.registerLanguage("yaml", yaml);
hljs.registerLanguage("ini", ini); // + toml alias
hljs.registerLanguage("rust", rust);
hljs.registerLanguage("go", go);
hljs.registerLanguage("c", c);
hljs.registerLanguage("cpp", cpp);
hljs.registerLanguage("java", java);
hljs.registerLanguage("bash", bash);
hljs.registerLanguage("markdown", markdown);
hljs.registerLanguage("xml", xml); // + html, svg, xhtml aliases
hljs.registerLanguage("css", css);
hljs.registerLanguage("sql", sql);
hljs.registerLanguage("diff", diff);
hljs.registerLanguage("dockerfile", dockerfile);

/**
 * Highlighting is a regex engine run over untrusted workspace content (attacker-controlled
 * per the threat model — benchmark tiers import arbitrary third-party repos). Adversarial
 * input can hang a regex highlighter, so anything past this size skips hljs entirely and
 * renders as escaped plain text instead. Measured as JS string length (UTF-16 code units) —
 * an approximation of byte size that is more than good enough for a "don't run a heavy
 * regex pass on a huge file" guard.
 */
export const HIGHLIGHT_MAX_BYTES = 256_000;

export interface HighlightResult {
  /** Pre-escaped HTML — either hljs's own output or our own `escapeHtml` fallback. */
  html: string;
  /** True when the input exceeded HIGHLIGHT_MAX_BYTES and hljs was skipped entirely. */
  capped: boolean;
  /** The language actually rendered with ("plaintext" when capped or unrecognized). */
  language: string;
}

/** Same escaping rule as the server's `html.escape(..., quote=True)` (see htmlpreview.py). */
export function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#x27;");
}

/**
 * The single capped-highlighting path shared by `CodeView` and `MarkdownView` (fenced code
 * blocks) — see 1B.4's "same capped hljs path" requirement. Never call `hljs.highlightAuto`
 * here: auto-detection runs every registered grammar against the input, which is exactly the
 * kind of unbounded work this cap exists to avoid, so the language is always explicit.
 */
export function highlightCode(code: string, language: string): HighlightResult {
  if (code.length > HIGHLIGHT_MAX_BYTES) {
    return { html: escapeHtml(code), capped: true, language: "plaintext" };
  }
  if (hljs.getLanguage(language)) {
    const result = hljs.highlight(code, { language, ignoreIllegals: true });
    return { html: result.value, capped: false, language };
  }
  return { html: escapeHtml(code), capped: false, language: "plaintext" };
}

const HTML_TAG_RE = /(<[^>]*>)/;
const ENTITY_RE = /&(?:amp|lt|gt|quot|#x27);/g;
const ENTITIES: Record<string, string> = {
  "&amp;": "&",
  "&lt;": "<",
  "&gt;": ">",
  "&quot;": '"',
  "&#x27;": "'",
};
const unescapeHtml = (value: string): string => value.replace(ENTITY_RE, (e) => ENTITIES[e]);

/** One highlighted line split into tag / decoded-text pieces so paths can be found in the text. */
type LinePiece = { tag: string } | { text: string };

function splitLine(lineHtml: string): LinePiece[] {
  return lineHtml
    .split(HTML_TAG_RE)
    .filter(Boolean)
    .map((part) => (part.startsWith("<") ? { tag: part } : { text: unescapeHtml(part) }));
}

/**
 * Rebuild a line's HTML with `<a class="path-link">` around every confirmed in-workspace path.
 * Only the SAME escaped text hljs/our fallback produced is re-emitted (`escapeHtml` on decoded
 * text), and the `href` is built by `encodeHash` from a validated tab, so nothing from the file
 * reaches the DOM unescaped. Paths that straddle a highlight span boundary stay plain text.
 */
function linkLine(
  pieces: LinePiece[],
  linkable: ReadonlyMap<string, string>,
  root: string | null,
): string {
  return pieces
    .map((piece) => {
      if ("tag" in piece) return piece.tag;
      let out = "";
      let cursor = 0;
      for (const c of findPathCandidates(piece.text, root)) {
        const href = linkable.get(c.path);
        if (!href) continue;
        out += escapeHtml(piece.text.slice(cursor, c.start));
        out += `<a class="path-link" href="${escapeHtml(href)}" data-path="${escapeHtml(c.path)}">${escapeHtml(c.raw)}</a>`;
        cursor = c.end;
      }
      return out + escapeHtml(piece.text.slice(cursor));
    })
    .join("");
}

/**
 * Highlighted code with line numbers (1B.3).
 *
 * Line numbers use the browser's native `<ol>` item marker (`::marker`), not text in the
 * code node — `::marker` content is excluded from text selection/copy in every evergreen
 * browser, so "select all, copy" never picks up the gutter.
 *
 * Path links (A4.5): workspace-relative path-like text in the file is linked to the file
 * viewer, but only after `POST /api/files/resolve` confirms the server sees a file/dir inside
 * the workspace -- the same probe `PathText` uses. Skipped for capped (huge) files.
 */
export function CodeView({ code, language }: { code: string; language: string }) {
  const { html, capped } = useMemo(() => highlightCode(code, language), [code, language]);
  const actions = useTabActions();
  const root = useWorkspaceRoot();

  // hljs escapes everything it tokenizes (and our own `escapeHtml` fallback covers the
  // capped/unrecognized-language path), so this is safe: dangerouslySetInnerHTML only ever
  // receives pre-escaped output from `highlightCode`, never raw file text directly.
  const pieces = useMemo(
    () => (capped ? null : html.split("\n").map(splitLine)),
    [html, capped],
  );
  const probePaths = useMemo(() => {
    const seen = new Set<string>();
    for (const line of pieces ?? []) {
      for (const piece of line) {
        if (!("text" in piece)) continue;
        for (const c of findPathCandidates(piece.text, root)) {
          if (seen.size >= MAX_CANDIDATES_PER_TEXT) return [...seen];
          seen.add(c.path);
        }
      }
    }
    return [...seen];
  }, [pieces, root]);
  const statuses = useResolvedPaths(probePaths);

  const lines = useMemo(() => {
    const linkable = new Map<string, string>();
    for (const path of probePaths) {
      const status = statuses.get(path);
      const tab = status === "file" || status === "dir" ? makeTab("file", { path }) : null;
      if (tab) linkable.set(path, encodeHash(tab));
    }
    if (!pieces) return html.split("\n");
    if (linkable.size === 0) return html.split("\n");
    return pieces.map((line) => linkLine(line, linkable, root));
    // `statuses` is a fresh Map each render, so key the memo on what it contributes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [html, pieces, probePaths, root, probePaths.map((p) => statuses.get(p)).join()]);

  const onClick = (event: MouseEvent<HTMLOListElement>) => {
    const anchor = (event.target as HTMLElement).closest?.("a.path-link");
    const path = anchor?.getAttribute("data-path");
    if (!path || event.shiftKey || event.altKey || event.button !== 0) return; // native
    event.preventDefault();
    const target = { kind: "file", params: { path } } as const;
    if ((event.ctrlKey || event.metaKey) && actions.available) actions.open(target, { activate: false });
    else actions.navigate(target);
  };
  const onAuxClick = (event: MouseEvent<HTMLOListElement>) => {
    const anchor = (event.target as HTMLElement).closest?.("a.path-link");
    const path = anchor?.getAttribute("data-path");
    if (!path || event.button !== 1) return;
    event.preventDefault();
    if (actions.available) actions.open({ kind: "file", params: { path } }, { activate: false });
  };

  return (
    <div className="code-block">
      {capped ? (
        <div className="banner info">
          File exceeds {formatBytes(HIGHLIGHT_MAX_BYTES)} — showing plain text without syntax
          highlighting.
        </div>
      ) : null}
      <ol className="code-lines" onClick={onClick} onAuxClick={onAuxClick}>
        {lines.map((lineHtml, index) => (
          <li key={index}>
            <code dangerouslySetInnerHTML={{ __html: lineHtml || "&nbsp;" }} />
          </li>
        ))}
      </ol>
    </div>
  );
}
