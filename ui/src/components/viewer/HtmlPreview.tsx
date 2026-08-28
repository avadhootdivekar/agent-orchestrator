import type { DroppedRef, HtmlPreview as HtmlPreviewData } from "../../types";

/**
 * Defence-in-depth CSP for the sandboxed document itself, on top of the server-side
 * stripping in `ui/htmlpreview.py`.
 *
 * Empirically verified (headless Chrome, `sandbox=""` iframe, this exact CSP string in a
 * `<meta http-equiv>` inside srcdoc): an inlined `<style>` block and a `data:image/png` both
 * render correctly — `style-src 'unsafe-inline' data:` and `img-src data:` are both doing
 * real work, not just permissive-by-accident. `script-src 'none'` plus the (separately
 * enforced) empty `sandbox` together mean nothing here can execute or reach the network —
 * every asset was already inlined server-side, so `connect-src 'none'` costs nothing.
 */
const PREVIEW_CSP =
  "default-src 'none'; img-src data:; style-src 'unsafe-inline' data:; font-src data:; " +
  "script-src 'none'; connect-src 'none'; form-action 'none'; base-uri 'none'";

const REASON_LABEL: Record<DroppedRef["reason"], string> = {
  external: "external reference",
  "outside-root": "outside the workspace root",
  "not-found": "not found",
  "too-large": "too large",
  "budget-exhausted": "inline budget exhausted",
  "unsupported-scheme": "unsupported URL scheme",
  "depth-exceeded": "import nesting too deep",
};

function groupByReason(dropped: DroppedRef[]): [string, DroppedRef[]][] {
  const groups = new Map<string, DroppedRef[]>();
  for (const ref of dropped) {
    const bucket = groups.get(ref.reason);
    if (bucket) bucket.push(ref);
    else groups.set(ref.reason, [ref]);
  }
  return Array.from(groups.entries());
}

/**
 * Sandboxed preview of a markup file (`.html`/`.svg`/etc), rendered from the server's already
 * sanitized, self-contained HTML (1A.2 / 1B.5).
 */
export function HtmlPreview({ preview }: { preview: HtmlPreviewData }) {
  const srcDoc = `<meta http-equiv="Content-Security-Policy" content="${PREVIEW_CSP}">\n${preview.html}`;

  return (
    <div className="html-preview">
      <div className="html-preview-meta muted">
        <span>
          {preview.scripts_removed} script{preview.scripts_removed === 1 ? "" : "s"} removed
        </span>
        <span>
          {preview.inlined} asset{preview.inlined === 1 ? "" : "s"} inlined
        </span>
        {preview.truncated ? <span>source truncated to the read limit</span> : null}
      </div>

      {preview.dropped.length > 0 ? (
        <details className="html-preview-dropped">
          <summary>{preview.dropped.length} reference(s) dropped</summary>
          {groupByReason(preview.dropped).map(([reason, refs]) => (
            <div key={reason} className="html-preview-dropped-group">
              <div className="secondary">{REASON_LABEL[reason as DroppedRef["reason"]] ?? reason}</div>
              <ul>
                {refs.map((ref, index) => (
                  // Text only, never a link/anchor — these are attacker-influenced strings
                  // from an untrusted workspace file, truncated server-side for display.
                  <li key={index} className="mono">
                    {ref.url}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </details>
      ) : null}

      <iframe
        // NO allow-same-origin, and NO allow-scripts: an empty `sandbox` forces an opaque
        // origin with script execution disabled — that opaque origin IS the security
        // boundary here. Setting BOTH allow-scripts and allow-same-origin together defeats
        // the sandbox entirely (the framed document could then script its way back out to
        // the real parent origin and its full workspace read + RCE + spend surface) — do
        // not add allow-same-origin here, ever, regardless of what a future feature wants.
        sandbox=""
        referrerPolicy="no-referrer"
        srcDoc={srcDoc}
        title={`Preview of ${preview.path}`}
        className="html-preview-frame"
      />
    </div>
  );
}
