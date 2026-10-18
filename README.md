# Competition Checker

Price monitoring of competitor e-shops. The repository currently holds two applications:

- **New application** (`backend/`, `frontend/`): FastAPI + PostgreSQL + React, built in stages
  according to [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md). Stage 1 (foundation, login) is done.
- **Old application** (`app.py`, Flask + SQLite): no longer deployed; kept for local use until stage 6 removes it.

## New application

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
and `cd frontend && npm test`. New migration after changing models:
`.venv/bin/alembic revision --autogenerate -m "description"`, then review the generated file.

### Deploy on Railway

The Railway service builds the new application from `deploy/Dockerfile`, as set in the root
`railway.json` (Railway config-as-code, supported until 2026-12-01; migrate with `railway config migrate`).

1. In the Railway project add **PostgreSQL** (*New → Database → PostgreSQL*).
2. Connect the service to the GitHub repository (branch `main`).
3. In the service's *Variables*:
   - `DATABASE_URL` = `${{Postgres.DATABASE_URL}}` (reference to the database service),
   - `SECRET_KEY` = random string of 48+ characters (`python3 -c "import secrets; print(secrets.token_urlsafe(48))"`),
   - `ADMIN_EMAIL` and `ADMIN_PASSWORD` for the first administrator.
4. *Settings → Networking → Generate Domain*, target port **8080**.

Each container start runs `python -m app.cli predeploy` before the web server: database migrations (under a database lock), then creates the first
administrator if none exists. Health check: `/readyz` (returns 503 when the database is unreachable).

## Old application

### Run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

Open http://localhost:5050 (set `PORT` to use another port; avoid 5000 on macOS, where AirPlay Receiver uses it).

### Deploy on Railway (old application, no longer deployed)

`railway.json` makes Railway start the app with gunicorn; `gunicorn.conf.py` binds it to `0.0.0.0:$PORT`.
The deploy log prints `Competition Checker listening on 0.0.0.0:<port>`. The public domain's target port
(*Settings → Networking*) must be that same port, otherwise Railway shows "Application failed to respond".

1. Push the repo and deploy it as a Railway service.
2. **Add a volume** to the service (any mount path, e.g. `/data`). The app stores its SQLite database there
   automatically (via `RAILWAY_VOLUME_MOUNT_PATH`). Without a volume, all data is lost on every redeploy.
3. Set `APP_PASSWORD` (required: without it the app stays locked on Railway) and optionally
   `APP_USERNAME` (default `admin`). Optionally set `SECRET_KEY` to a random string.
4. Under *Settings → Networking*, click *Generate Domain* to get a public URL.

The browser asks for the username and password (HTTP Basic Auth). `/healthz` stays open for Railway's health check.

### How to use

1. **Our products**: add products by hand, or import a CSV with the columns `name`, `price` and optionally `sku` and `currency`.
2. **Competitors**: add a competitor with one or more page URLs (category/listing pages or single product pages). The app scrapes them straight away.
   - It first reads the schema.org product data (JSON-LD or microdata) or Open Graph price tags that most e-shops publish.
   - If it finds nothing, open the competitor's *Settings* and give CSS selectors for the product card, the name and the price.
3. **Comparison**: each of your products is matched by name to the most similar competitor product and shown with its price difference in %. Red means the competitor is cheaper. If a match is wrong, fix it on the competitor's page (pick a product or *Ignore*). Manual matches are kept when you re-scrape.
4. Use **Refresh all competitors** to scrape again and **Export CSV** to download the table.

### Notes

- Data is stored in `competition.db` (SQLite). Set `COMPETITION_DB` to use a different path.
- The app follows `robots.txt` and fetches one page per URL. Pages that build their product list with JavaScript only may need the CSS-selector option, or may not work at all.
- Tests: `.venv/bin/python -m unittest discover -s tests`
