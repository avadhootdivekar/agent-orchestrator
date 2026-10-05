import { render } from "@testing-library/react";
import qrcode from "qrcode-generator";
import { describe, expect, it } from "vitest";
import QrCode from "../auth/QrCode";
import { QR_ECC_LEVEL, QR_QUIET_ZONE_MODULES, QR_RENDER_PX } from "../auth/constants";
import qrSource from "../auth/QrCode.tsx?raw";

const URI =
  "otpauth://totp/ao%40devbox:alice?secret=JBSWY3DPEHPK3PXP&issuer=ao%40devbox&algorithm=SHA1&digits=6&period=30";

describe("QrCode (real qrcode-generator)", () => {
  it("renders exactly one role=img svg with a module-accurate viewBox and a non-empty path", () => {
    const { container } = render(<QrCode uri={URI} />);
    const svgs = container.querySelectorAll("svg");
    expect(svgs).toHaveLength(1);
    const svg = svgs[0];
    expect(svg).toHaveAttribute("role", "img");
    expect(svg).toHaveAttribute("aria-label", "QR code for the setup URI");

    const reference = qrcode(0, QR_ECC_LEVEL);
    reference.addData(URI);
    reference.make();
    const side = reference.getModuleCount() + 2 * QR_QUIET_ZONE_MODULES;
    expect(svg.getAttribute("viewBox")).toBe(`0 0 ${side} ${side}`);
    expect(svg).toHaveAttribute("width", String(QR_RENDER_PX));

    const paths = container.querySelectorAll("path");
    expect(paths).toHaveLength(1);
    const d = paths[0].getAttribute("d") ?? "";
    expect(d.length).toBeGreaterThan(0);
    // One unit square per dark module, offset by the quiet zone; the corner finder pattern is dark.
    expect(d).toContain(`M${QR_QUIET_ZONE_MODULES} ${QR_QUIET_ZONE_MODULES}h1v1h-1z`);
    let dark = 0;
    for (let r = 0; r < reference.getModuleCount(); r += 1) {
      for (let c = 0; c < reference.getModuleCount(); c += 1) if (reference.isDark(r, c)) dark += 1;
    }
    expect(d.split("z").length - 1).toBe(dark);
  });

  it("is black on white regardless of theme", () => {
    const { container } = render(<QrCode uri={URI} />);
    expect(container.querySelector("rect")).toHaveAttribute("fill", "#fff");
    expect(container.querySelector("path")).toHaveAttribute("fill", "#000");
  });

  it("never uses the library's HTML-string helpers or dangerouslySetInnerHTML (source check)", () => {
    for (const banned of ["dangerouslySetInnerHTML", "createSvgTag", "createImgTag"]) {
      expect(qrSource).not.toContain(banned);
    }
  });

  it("is the only importer of qrcode-generator under ui/src", () => {
    const sources = import.meta.glob("../**/*.{ts,tsx}", { query: "?raw", import: "default", eager: true }) as Record<
      string,
      string
    >;
    const importers = Object.entries(sources)
      .filter(([path, text]) => !path.includes("/test/") && /from\s+["']qrcode-generator["']/.test(text))
      .map(([path]) => path);
    expect(importers).toEqual(["../auth/QrCode.tsx"]);
  });
});
