import { useState } from "react";
import {
  formatCount,
  formatPromptSource,
  formatShortSha,
  formatTimestamp,
} from "../format";
import type { RunPrompt } from "../types";
import { CollapsibleSection } from "./CollapsibleSection";
import { PathLink, PathText } from "./PathText";

/** How long the copy button keeps saying "Copied" before it resets. */
const COPIED_RESET_MS = 1500;

/**
 * The prompt a run started with (E-Us9Kd4 FR-13).
 *
 * The text is untrusted (it can be anything a user or file contained), so it is rendered ONLY
 * as React text children inside a `white-space: pre-wrap` block: no markdown, no HTML, no
 * `dangerouslySetInnerHTML`. Collapsible via the shared `CollapsibleSection`, open by default.
 */
export function PromptPanel({
  prompt,
  changed,
}: {
  prompt: RunPrompt | null;
  changed?: boolean | null;
}) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    if (!prompt) return;
    try {
      await navigator.clipboard.writeText(prompt.text);
      setCopied(true);
      setTimeout(() => setCopied(false), COPIED_RESET_MS);
    } catch {
      // Clipboard can be unavailable (insecure context, denied permission); the text stays
      // selectable in the block, so failing quietly loses nothing.
    }
  };

  return (
    <CollapsibleSection id="prompt" title="Prompt" className="prompt-panel">
      {prompt === null ? (
        <div className="muted prompt-none">No prompt recorded for this run</div>
      ) : (
        <div className="stack" style={{ gap: 8, marginTop: 8 }}>
          <div className="row prompt-meta" style={{ gap: 8, flexWrap: "wrap" }}>
            <span className="tag">{formatPromptSource(prompt.source)}</span>
            <span className="muted">{formatCount(prompt.chars)} chars</span>
            <span className="mono muted" title={prompt.sha256}>
              sha256 {formatShortSha(prompt.sha256)}
            </span>
            <span className="mono muted">
              <PathLink path={prompt.path} />
            </span>
            <span className="muted">
              captured {formatTimestamp(prompt.captured_at)}
            </span>
            <button type="button" onClick={() => void copy()}>
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
          {prompt.truncated ? (
            <div className="banner info" role="note">
              Truncated: showing the first {formatCount(prompt.text.length)} of{" "}
              {formatCount(prompt.chars)} characters.
            </div>
          ) : null}
          {changed ? (
            <div className="banner info" role="note">
              The prompt file has changed since this run started; the text below
              is what the run began with.
            </div>
          ) : null}
          <pre className="prompt-text">
            <PathText text={prompt.text} />
          </pre>
        </div>
      )}
    </CollapsibleSection>
  );
}
