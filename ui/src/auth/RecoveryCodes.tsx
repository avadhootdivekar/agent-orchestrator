import { useState } from "react";
import { BLOB_URL_REVOKE_DELAY_MS, RECOVERY_CODES_FILENAME } from "./constants";
import { CopyButton } from "./CopyButton";

interface Props {
  codes: string[];
  /** Called only after the user ticks "I have stored these codes". */
  onContinue(): void;
}

/** Offer `codes` as a text file; the Blob URL is revoked after use (never left dangling). */
function downloadCodes(codes: string[]): void {
  const url = URL.createObjectURL(new Blob([`${codes.join("\n")}\n`], { type: "text/plain" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = RECOVERY_CODES_FILENAME;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), BLOB_URL_REVOKE_DELAY_MS);
}

/** Recovery codes, shown once (HLD §14.3): list, copy, download, and an acknowledgement gate. */
export function RecoveryCodes({ codes, onContinue }: Props) {
  const [stored, setStored] = useState(false);
  return (
    <>
      <p className="auth-lede">
        Save these recovery codes somewhere safe. Each works once if you lose your authenticator, and
        they will not be shown again.
      </p>
      <ol className="recovery-codes" aria-label="Recovery codes">
        {codes.map((code) => (
          <li key={code}>
            <code>{code}</code>
          </li>
        ))}
      </ol>
      <div className="auth-actions">
        <CopyButton text={codes.join("\n")} label="Copy all" />
        <button type="button" onClick={() => downloadCodes(codes)}>
          Download .txt
        </button>
      </div>
      <label className="auth-check">
        <input type="checkbox" checked={stored} onChange={(e) => setStored(e.target.checked)} />I have
        stored these codes
      </label>
      <div className="auth-actions">
        <button type="button" className="primary" disabled={!stored} onClick={onContinue}>
          Continue
        </button>
      </div>
    </>
  );
}
