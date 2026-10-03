# Ringwise: one image serves the Flask API and the built React SPA (Railway).
# Elastic Beanstalk does not use this file; see backend/Procfile and backend/scripts/build_eb_bundle.sh.

# ---------------------------------------------------------------- stage 1: build the React app
FROM node:22-slim AS frontend
WORKDIR /app/frontend
ENV CI=true
# Lockfile first so the npm ci layer is cached until dependencies change.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------- stage 2: Flask + gunicorn
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Unprivileged user with a home directory (gunicorn keeps its control socket in ~/.gunicorn).
RUN useradd --create-home --uid 10001 ringwise

WORKDIR /app/backend
COPY backend/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY backend/ /app/backend/
# app/spa.py looks for <repo root>/frontend/dist, which is /app/frontend/dist here.
COPY --from=frontend /app/frontend/dist /app/frontend/dist

USER ringwise
EXPOSE 8080

# Migrations run first under a Postgres advisory lock (safe with several replicas),
# then gunicorn binds the port Railway injects.
CMD ["sh", "-c", "python scripts/migrate.py && exec gunicorn wsgi:app --bind 0.0.0.0:${PORT:-8080} --workers 2 --threads 4 --timeout 30"]
