import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { VitePWA } from "vite-plugin-pwa";

// The API the app talks to. In development it is the uvicorn process next
// door; in production both come from the same origin behind the tunnel.
const API_TARGET = process.env.TEMPO_API ?? "http://127.0.0.1:8000";

// Never precached, never cached at runtime. See the manifestTransforms
// below and the no-store headers tempo/api/static.py sends for the same
// two files.
const EXCLUDED_FROM_PRECACHE = /(^|\/)(sw\.js|manifest\.webmanifest)$/;

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      registerType: "prompt",
      // The two files that must never be served from a cache: a cached
      // service worker cannot be replaced, and a cached manifest freezes
      // the install prompt on whatever it said the first time.
      injectRegister: null,
      // The manifest is a static file in public/, not generated here.
      // vite-plugin-pwa appends a generated manifest to
      // additionalManifestEntries, and workbox-build applies those *after*
      // every manifestTransform — so a generated manifest cannot be kept
      // out of the precache, and docs/PLAN.md says it must be. A file in
      // public/ that no glob pattern matches is the way to obey that.
      manifest: false,
      workbox: {
        // The manifest is deliberately absent from this list: a precached
        // manifest is served from the cache forever, and the install
        // prompt then keeps whatever it said the first time.
        globPatterns: ["**/*.{js,css,html,svg,png}"],
        // Never precache these two: see above. Workbox would otherwise put
        // the manifest in the precache and serve it from there forever.
        globIgnores: ["sw.js", "**/sw.js", "**/workbox-*.js"],
        // Belt and braces for the same rule: whatever ends up in the list,
        // these two never stay in it.
        manifestTransforms: [
          async (entries) => ({
            manifest: entries.filter(
              (entry) => !EXCLUDED_FROM_PRECACHE.test(entry.url),
            ),
            warnings: [],
          }),
        ],
        navigateFallback: "/index.html",
        navigateFallbackDenylist: [/^\/api/, /^\/health/, /^\/docs/],
        cleanupOutdatedCaches: true,
        runtimeCaching: [
          {
            // Stale-while-revalidate for the API: the last state stays
            // readable offline, and the interface says how old it is —
            // which is why the response date is kept alongside the body.
            //
            // Except when the app asks for fresh data. After a mutation —
            // a session confirmed, a workout sent — the reload that follows
            // must not be answered from the cache, or the screen shows the
            // state from before the action that just succeeded. Those
            // requests carry Cache-Control: no-cache and fall through to
            // the network.
            urlPattern: ({ url, request }) =>
              url.pathname.startsWith("/api/") &&
              request.headers.get("cache-control") !== "no-cache",
            handler: "StaleWhileRevalidate",
            method: "GET",
            options: {
              cacheName: "tempo-api",
              expiration: { maxEntries: 64, maxAgeSeconds: 60 * 60 * 24 * 30 },
              cacheableResponse: { statuses: [200] },
            },
          },
          {
            urlPattern: ({ url }) =>
              url.pathname === "/manifest.webmanifest" || url.pathname === "/sw.js",
            handler: "NetworkOnly",
          },
        ],
      },
      devOptions: { enabled: false },
    }),
  ],
  server: {
    proxy: {
      "/api": { target: API_TARGET, changeOrigin: true },
      "/health": { target: API_TARGET, changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
  test: {
    environment: "jsdom",
    globals: true,
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
  },
});
