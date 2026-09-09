#!/usr/bin/env bash
# Start the built image and prove it serves the app.
#
# `docker build` succeeding says nothing about whether the app is
# reachable: the frontend can be missing from the image and every other
# step still passes. That is what happened, and this is the check that
# would have caught it.
#
# Usage: scripts/smoke-image.sh [image] [port]

set -euo pipefail

IMAGE="${1:-tempo:ci}"
PORT="${2:-8123}"
NAME="tempo-smoke-$$"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cleanup() {
    docker rm -f "$NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "→ starting $IMAGE"
docker run -d --name "$NAME" -p "127.0.0.1:${PORT}:8000" "$IMAGE" >/dev/null

base="http://127.0.0.1:${PORT}"
echo "→ waiting for the container"
for attempt in $(seq 1 40); do
    if curl -fsS "${base}/health" >/dev/null 2>&1; then
        break
    fi
    if [ "$attempt" -eq 40 ]; then
        echo "the container never became healthy" >&2
        docker logs "$NAME" >&2
        exit 1
    fi
    sleep 1
done

if ! "${HERE}/smoke-http.sh" "$base"; then
    docker logs "$NAME" >&2
    exit 1
fi
