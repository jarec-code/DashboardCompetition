from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

from app.api import auth, users
from app.api.deps import csrf_protect
from app.config import get_settings
from app.db import engine

settings = get_settings()

app = FastAPI(title="Competition Checker", docs_url="/api/docs" if not settings.is_production else None,
              redoc_url=None, openapi_url="/api/openapi.json" if not settings.is_production else None)
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, session_cookie="cc_session",
                   max_age=settings.session_max_age_seconds, same_site="lax", https_only=settings.is_production)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if settings.is_production:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return response


api = APIRouter(prefix="/api", dependencies=[Depends(csrf_protect)])
api.include_router(users.router)
app.include_router(auth.router, prefix="/api")  # login works without a CSRF token; others check it
app.include_router(api)


@app.get("/healthz", include_in_schema=False)
def healthz() -> PlainTextResponse:
    """Liveness: the process is running."""
    return PlainTextResponse("ok")


@app.get("/readyz", include_in_schema=False)
def readyz() -> JSONResponse:
    """Readiness: the database is reachable."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse({"status": "error", "database": "unavailable"}, status_code=503)
    return JSONResponse({"status": "ok"})


# ---------- React frontend (built into frontend/dist) ----------

dist = settings.frontend_dist
if (dist / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/"):
        raise HTTPException(404, "Not found")
    candidate = (dist / path).resolve()
    if path and candidate.is_file() and candidate.is_relative_to(dist.resolve()):
        return FileResponse(candidate)
    index = dist / "index.html"
    if index.is_file():
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    return PlainTextResponse("Frontend nie je zostavený: spustite `npm run build` v priečinku frontend/.", 503)
