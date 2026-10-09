import { PathText } from "./PathText";

/**
 * The captured CLI log of a run with server-verified file paths auto-linked (A5). Same
 * `<pre className="code">` block as before; paths are only linked when `POST /api/files/resolve`
 * confirms them, everything else (and all non-path text) stays literal.
 */
export function RunLog({ text }: { text: string }) {
  return (
    <pre className="code">
      <PathText text={text} />
    </pre>
  );
}
