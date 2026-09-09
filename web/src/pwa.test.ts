/**
 * The PWA rules, checked against the build rather than against the config.
 *
 * docs/PLAN.md is unambiguous: sw.js and manifest.webmanifest may not be
 * cached. The config expresses that in three ways at once, and a plugin
 * update could quietly undo any of them — vite-plugin-pwa appends the
 * generated manifest to the precache *after* every transform runs, which
 * is exactly how it got in the first time. So the check reads the finished
 * artefact.
 *
 * Skipped when there is no build: `npm run build && npm test` runs it,
 * a bare `npm test` says so rather than failing for the wrong reason.
 */

import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const dist = resolve(__dirname, "..", "dist");
const built = existsSync(resolve(dist, "sw.js"));

describe.skipIf(!built)("the built service worker", () => {
  const worker = () => readFileSync(resolve(dist, "sw.js"), "utf-8");

  it("does not precache itself", () => {
    expect(worker()).not.toContain('url:"sw.js"');
  });

  it("does not precache the manifest", () => {
    expect(worker()).not.toContain('url:"manifest.webmanifest"');
  });

  it("precaches the shell and the hashed assets", () => {
    const text = worker();
    expect(text).toContain('url:"index.html"');
    expect(text).toContain("assets/index-");
  });

  it("keeps the API on stale-while-revalidate", () => {
    // The last state stays readable offline; the screens date what they show.
    expect(worker()).toContain("StaleWhileRevalidate");
  });
});

describe.skipIf(!built)("the built manifest", () => {
  it("declares a standalone app with both icon sizes", () => {
    const manifest = JSON.parse(
      readFileSync(resolve(dist, "manifest.webmanifest"), "utf-8"),
    ) as { display: string; icons: { sizes: string; purpose?: string }[] };

    expect(manifest.display).toBe("standalone");
    expect(manifest.icons.map((icon) => icon.sizes)).toContain("192x192");
    expect(manifest.icons.map((icon) => icon.sizes)).toContain("512x512");
    expect(manifest.icons.some((icon) => icon.purpose === "maskable")).toBe(true);
  });

  it("is linked from the shell, with the apple touch icon and the safe areas", () => {
    const html = readFileSync(resolve(dist, "index.html"), "utf-8");

    expect(html).toContain('rel="manifest"');
    expect(html).toContain('rel="apple-touch-icon"');
    // Without viewport-fit=cover the safe-area insets are all zero.
    expect(html).toContain("viewport-fit=cover");
  });
});
