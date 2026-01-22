#!/usr/bin/env bash
set -euo pipefail

IMAGE="${IMAGE:-yt-transfer}"
COOKIE_PATH="${COOKIE_PATH:-$PWD/../cookies.json}"

touch "$COOKIE_PATH"

echo "Using cookies at $COOKIE_PATH"
echo "Launching biliup login inside $IMAGE..."
docker run --rm -it \
  -v "$COOKIE_PATH:/app/cookies.json" \
  --entrypoint /bin/sh \
  "$IMAGE" \
  -c 'if command -v biliup >/dev/null 2>&1; then biliup --user-cookie /app/cookies.json login "$@"; \
      elif python3 -c "import biliup" >/dev/null 2>&1; then python3 -m biliup --user-cookie /app/cookies.json login "$@"; \
      else echo "biliup not found in image. Rebuild with: docker build -t '"$IMAGE"' ."; exit 1; fi' sh "$@"
