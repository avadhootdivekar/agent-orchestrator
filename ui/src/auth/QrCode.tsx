import qrcode from "qrcode-generator";
import { useMemo } from "react";
import { QR_ECC_LEVEL, QR_QUIET_ZONE_MODULES, QR_RENDER_PX } from "./constants";

/**
 * The ONLY importer of `qrcode-generator` (HLD §17.7), default-exported so `EnrollScreen` can load
 * it with `React.lazy` and keep the library out of the main chunk. We use only `addData`, `make`,
 * `getModuleCount` and `isDark`, and draw our own `<svg><path>`: the library's HTML-string helpers
 * are never used, so no markup from it ever reaches the DOM.
 */
interface QrShape {
  /** Side length in modules, quiet zone included (the `viewBox` side). */
  side: number;
  /** One `<path d>` covering every dark module. */
  path: string;
}

function buildQr(data: string): QrShape {
  const code = qrcode(0, QR_ECC_LEVEL); // type 0: smallest version that fits the data
  code.addData(data);
  code.make();
  const count = code.getModuleCount();
  const quiet = QR_QUIET_ZONE_MODULES;
  let path = "";
  for (let row = 0; row < count; row += 1) {
    for (let col = 0; col < count; col += 1) {
      if (code.isDark(row, col)) path += `M${col + quiet} ${row + quiet}h1v1h-1z`;
    }
  }
  return { side: count + 2 * quiet, path };
}

/** Black on white in BOTH themes: scanners need the contrast (the one documented token exception). */
export default function QrCode({ uri }: { uri: string }) {
  const shape = useMemo(() => {
    try {
      return buildQr(uri);
    } catch {
      return null; // never happens for an otpauth URI; the text secret + URI are always shown too
    }
  }, [uri]);
  if (!shape) return null;
  return (
    <svg
      role="img"
      aria-label="QR code for the setup URI"
      width={QR_RENDER_PX}
      height={QR_RENDER_PX}
      viewBox={`0 0 ${shape.side} ${shape.side}`}
      shapeRendering="crispEdges"
    >
      <rect width={shape.side} height={shape.side} fill="#fff" />
      <path d={shape.path} fill="#000" />
    </svg>
  );
}
