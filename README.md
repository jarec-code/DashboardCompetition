# Competition Checker

Price monitoring of competitor e-shops: FastAPI + PostgreSQL + React, built in stages according to
[docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md). Writing a parser for a new website:
[docs/SCRAPER_GUIDE.md](docs/SCRAPER_GUIDE.md). (The earlier Flask prototype was removed in stage 6.)

## Application

### Local development

Requirements: Python 3.12, Node.js 24, PostgreSQL 16 (`brew install python@3.12 node postgresql@16`).

```bash
createdb competition && createdb competition_test
cp .env.example .env                      # adjust if your database URL differs

cd backend
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m app.cli migrate
.venv/bin/python -m app.cli create-admin --email you@example.com   # asks for a password (min. 10 chars)
.venv/bin/uvicorn app.main:app --reload --port 8000

cd ../frontend
npm install
npm run dev                               # http://localhost:5173 (proxies /api to port 8000)
```

Tests: `cd backend && .venv/bin/pytest` (uses the `competition_test` database, or `TEST_DATABASE_URL`)
and `cd frontend && npm test`. Browser tests: `createdb competition_e2e`, `npx playwright install chromium` once,
then `cd frontend && npm run build && npm run e2e` (starts its own server on port 8010 with a fresh e2e database). New migration after changing models:
`.venv/bin/alembic revision --autogenerate -m "description"`, then review the generated file.

### Deploy on Railway

The Railway service builds the application from the root `Dockerfile` (detected automatically). The root
`railway.json` adds the health check `/readyz` and the restart policy; Railway reads such files only until
2026-12-01, after that set the health check in *Settings → Deploy* or migrate with `railway config migrate`.

1. In the Railway project add **PostgreSQL** (*New → Database → PostgreSQL*).
2. Connect the service to the GitHub repository (branch `main`).
3. In the service's *Variables*:
   - `DATABASE_URL` = `${{Postgres.DATABASE_URL}}` (reference to the database service),
   - `SECRET_KEY` = random string of 48+ characters (`python3 -c "import secrets; print(secrets.token_urlsafe(48))"`),
   - `ADMIN_EMAIL` and `ADMIN_PASSWORD` for the first administrator.
4. *Settings → Networking → Generate Domain*, target port **8080**.

Each container start runs `python -m app.cli predeploy` before the web server: database migrations (under a database lock), then creates the first
administrator if none exists. Health check: `/readyz` (returns 503 when the database is unreachable).
