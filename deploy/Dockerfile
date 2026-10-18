# Image for the new application (web; later also the worker). Build context: repository root.
# Kept outside the repository root on purpose: a root Dockerfile would also be picked up by the
# old Flask service on Railway and break it.

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
# Railway sets PORT; proxy headers give the real client IP for the login rate limit.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'"]
