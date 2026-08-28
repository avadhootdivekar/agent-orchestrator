import { useMemo } from "react";
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

/**
 * Highlighted code with line numbers (1B.3).
 *
 * Line numbers use the browser's native `<ol>` item marker (`::marker`), not text in the
 * code node — `::marker` content is excluded from text selection/copy in every evergreen
 * browser, so "select all, copy" never picks up the gutter.
 */
export function CodeView({ code, language }: { code: string; language: string }) {
  const { html, capped } = useMemo(() => highlightCode(code, language), [code, language]);

  // hljs escapes everything it tokenizes (and our own `escapeHtml` fallback covers the
  // capped/unrecognized-language path), so this is safe: dangerouslySetInnerHTML only ever
  // receives pre-escaped output from `highlightCode`, never raw file text directly.
  const lines = useMemo(() => html.split("\n"), [html]);

  return (
    <div className="code-block">
      {capped ? (
        <div className="banner info">
          File exceeds {formatBytes(HIGHLIGHT_MAX_BYTES)} — showing plain text without syntax
          highlighting.
        </div>
      ) : null}
      <ol className="code-lines">
        {lines.map((lineHtml, index) => (
          <li key={index}>
            <code dangerouslySetInnerHTML={{ __html: lineHtml || "&nbsp;" }} />
          </li>
        ))}
      </ol>
    </div>
  );
}
