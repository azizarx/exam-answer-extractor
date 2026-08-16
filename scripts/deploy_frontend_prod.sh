#!/usr/bin/env bash
# Run ON the prod box as auto-deploy (or root).
# Pulls latest main, rebuilds frontend with aimarker-bk API URL, refreshes nginx dist.
set -euo pipefail

REPO_DIR="${REPO_DIR:-$HOME/aimarker}"
DIST_DIR="${DIST_DIR:-$HOME/do-not-delete/frontend/dist}"
API_URL="${VITE_API_BASE_URL:-https://aimarker-bk.seamo-official.org}"

if [[ ! -d "$REPO_DIR/.git" ]]; then
  # common layouts
  for d in "$HOME/aimarker" "$HOME/AI-server/aimarker" "$HOME/do-not-delete/aimarker" /var/www/aimarker; do
    if [[ -d "$d/.git" ]]; then REPO_DIR="$d"; break; fi
  done
fi

echo "==> repo: $REPO_DIR"
cd "$REPO_DIR"
git fetch origin
git checkout main
git pull --ff-only origin main

echo "==> rebuild frontend (API=$API_URL)"
cd frontend
if [[ -f build-and-copy.sh ]]; then
  # Dockerfile bakes VITE_API_BASE_URL; ensure ARG default is correct in tree
  bash ./build-and-copy.sh
else
  docker build \
    --build-arg "VITE_API_BASE_URL=$API_URL" \
    -t aimarker-frontend-build .
  cid=$(docker create aimarker-frontend-build)
  rm -rf "$DIST_DIR"
  mkdir -p "$DIST_DIR"
  docker cp "$cid:/app/dist/." "$DIST_DIR/"
  docker rm "$cid" >/dev/null
fi

echo "==> verify baked API host"
if grep -R -l 'localhost:8000' "$DIST_DIR" >/dev/null 2>&1; then
  echo "WARNING: dist still contains localhost:8000"
  grep -R -o 'localhost:8000\|aimarker-bk[^\" ]*' "$DIST_DIR"/assets/*.js 2>/dev/null | sort -u | head
else
  echo "OK: no localhost:8000 in dist"
  grep -R -o 'aimarker-bk\.seamo-official\.org' "$DIST_DIR"/assets/*.js 2>/dev/null | head -3
fi

# optional API container refresh
if command -v docker >/dev/null && [[ -f "$REPO_DIR/docker-compose.yml" ]]; then
  echo "==> docker compose up API"
  cd "$REPO_DIR"
  docker compose pull 2>/dev/null || true
  docker compose up -d --build app
fi

echo "==> done. Hard-refresh https://aimarker.seamo-official.org/"
