#!/bin/bash
# Elastic Beanstalk predeploy hook: runs on every instance, from the staging directory, before
# the new version starts serving. scripts/migrate.py holds a Postgres advisory lock while it
# runs `flask db upgrade`, so instances deploying at the same time take turns and every run
# after the first is a no-op. A non-zero exit aborts the deployment.
set -euo pipefail

# Use the platform's application venv, not /usr/bin/python3 (system Python on AL2023).
VENV_BIN="$(ls -d /var/app/venv/staging-*/bin 2>/dev/null | head -n 1)"
if [ -z "${VENV_BIN}" ]; then
  echo "01_migrate: application venv not found under /var/app/venv" >&2
  exit 1
fi

cd /var/app/staging
"${VENV_BIN}/python" scripts/migrate.py
