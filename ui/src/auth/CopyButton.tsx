import { useState } from "react";

type CopyState = "idle" | "copied" | "failed";

/** Copy `text` to the clipboard. Clipboard access can be denied (insecure context, permissions), so say so. */
export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [state, setState] = useState<CopyState>("idle");

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setState("copied");
    } catch {
      setState("failed");
    }
  }

  return (
    <>
      <button type="button" onClick={() => void copy()}>
        {label}
      </button>
      <span role="status" className="auth-copy-state">
        {state === "copied" ? "Copied" : state === "failed" ? "Copy failed: select and copy it by hand" : ""}
      </span>
    </>
  );
}
