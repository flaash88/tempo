#!/usr/bin/env bash
# The assertions: is the app actually being served, at this address?
#
# Split out from the image smoke test so the same checks can be pointed at
# a container, at a local uvicorn, or at the real deployment behind the
# tunnel. What is checked is the thing that was silently missing — a build
# whose result is not retrievable is not a green build.
#
# Usage: scripts/smoke-http.sh [base-url]

set -euo pipefail

BASE="${1:-http://127.0.0.1:8000}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fail() {
    echo "FAIL: $1" >&2
    exit 1
}

# 1. The app itself. This is the check that was missing: an image whose
#    frontend never made it in answers 404 here and passed CI anyway.
status=$(curl -s -o "$TMP/root.html" -w "%{http_code}" "${BASE}/")
[ "$status" = "200" ] || fail "GET / answered $status"
grep -qi "<!doctype html" "$TMP/root.html" || fail "GET / returned no HTML"
grep -q 'id="root"' "$TMP/root.html" || fail "GET / is not the app shell"
echo "✓ GET /                      200, HTML"

# 2. The manifest, uncached. Without it there is no install prompt, and a
#    cached one freezes it on whatever it said the first time.
curl -s -o "$TMP/manifest.json" -D "$TMP/manifest.head" "${BASE}/manifest.webmanifest"
head -1 "$TMP/manifest.head" | grep -q " 200" || fail "GET /manifest.webmanifest: $(head -1 "$TMP/manifest.head")"
grep -q '"display"' "$TMP/manifest.json" || fail "the manifest carries no display mode"
grep -qi "cache-control:.*no-store" "$TMP/manifest.head" || fail "the manifest is cacheable"
echo "✓ GET /manifest.webmanifest  200, no-store"

# 3. The service worker, uncached. A cached one can never be replaced, and
#    the old app is then served for good.
curl -s -o "$TMP/sw.js" -D "$TMP/sw.head" "${BASE}/sw.js"
head -1 "$TMP/sw.head" | grep -q " 200" || fail "GET /sw.js: $(head -1 "$TMP/sw.head")"
grep -qi "cache-control:.*no-store" "$TMP/sw.head" || fail "the service worker is cacheable"
[ -s "$TMP/sw.js" ] || fail "the service worker is empty"
echo "✓ GET /sw.js                 200, no-store"

# 4. A route of the app is the app; a path of the server's stays the
#    server's, including when it does not exist.
status=$(curl -s -o /dev/null -w "%{http_code}" "${BASE}/trends")
[ "$status" = "200" ] || fail "GET /trends answered $status"
status=$(curl -s -o /dev/null -w "%{http_code}" "${BASE}/api/gibt-es-nicht")
[ "$status" = "404" ] || fail "GET /api/gibt-es-nicht answered $status"
status=$(curl -s -o /dev/null -w "%{http_code}" "${BASE}/health")
[ "$status" = "200" ] || fail "GET /health answered $status"
echo "✓ SPA-Fallback, API-404, /health"

echo "the app is being served at ${BASE}"
