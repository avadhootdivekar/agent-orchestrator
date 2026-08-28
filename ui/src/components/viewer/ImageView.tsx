import { formatBytes } from "../../format";
import type { FileContent } from "../../types";

/**
 * `kind: "image"` viewer (1B.2). `data_uri` comes from the server's extension-allowlisted,
 * magic-byte-verified classification (files.py) — never re-derived or re-sniffed here.
 */
export function ImageView({ content }: { content: FileContent }) {
  if (!content.data_uri) {
    return (
      <div className="banner info">
        Image is {formatBytes(content.size)} — over the inline preview cap, so it is not shown
        here.
      </div>
    );
  }
  return (
    <div className="image-view">
      <img src={content.data_uri} alt={content.path} />
    </div>
  );
}
