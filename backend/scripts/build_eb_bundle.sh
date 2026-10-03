#!/bin/bash
# Build an Elastic Beanstalk source bundle: the backend at the bundle root (Procfile,
# requirements.txt, .platform, .ebextensions, cron.yaml) plus the built React app at
# frontend/dist, which app/spa.py serves. Output: build/ringwise-eb.zip at the repo root.
#
#   ./backend/scripts/build_eb_bundle.sh            # builds the frontend first
#   SKIP_FRONTEND_BUILD=1 ./backend/scripts/build_eb_bundle.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="${ROOT}/build/eb"
ZIP="${ROOT}/build/ringwise-eb.zip"

if [ "${SKIP_FRONTEND_BUILD:-0}" != "1" ]; then
  (cd "${ROOT}/frontend" && npm ci && npm run build)
fi
if [ ! -f "${ROOT}/frontend/dist/index.html" ]; then
  echo "frontend/dist/index.html is missing; build the frontend first" >&2
  exit 1
fi

rm -rf "${OUT}" "${ZIP}"
mkdir -p "${OUT}/frontend"
rsync -a \
  --exclude '.venv' --exclude '__pycache__' --exclude '.pytest_cache' \
  --exclude 'tests' --exclude '.env' --exclude 'scripts/build_eb_bundle.sh' \
  "${ROOT}/backend/" "${OUT}/"
cp -R "${ROOT}/frontend/dist" "${OUT}/frontend/dist"
chmod +x "${OUT}/.platform/hooks/predeploy/"*.sh
(cd "${OUT}" && zip -qr "${ZIP}" . -x '*.DS_Store')
echo "Wrote ${ZIP}"
