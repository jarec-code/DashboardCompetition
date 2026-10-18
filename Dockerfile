# Image of the application (web + embedded worker; `python -m app.worker` for a separate worker service).
# Railway detects this root Dockerfile on its own, even without railway.json.

FROM node:24-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt
COPY backend/ ./
COPY --from=frontend /build/dist /app/frontend/dist
RUN useradd --create-home --uid 1000 app
USER app

ENV PORT=8080
EXPOSE 8080
# Migrations and the first admin run before the server starts (guarded by a database lock), so the
# app never serves requests against an outdated schema. Railway sets PORT; proxy headers give the
# real client IP for the login rate limit.
CMD ["sh", "-c", "python -m app.cli predeploy && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'"]
