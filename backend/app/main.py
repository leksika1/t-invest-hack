import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.cards.router import router as cards_router
from app.config import get_settings
from app.db import check_db
from app.iss.router import router as iss_router
from app.errors import ProcessingError
from app.issues.router import router as issues_router
from app.jobs.worker import run_worker
from app.middleware import UploadLimitMiddleware
from app.rate_limit import RateLimitMiddleware
from app.admin.router import router as admin_router

settings = get_settings()


@asynccontextmanager
async def lifespan(app):
    stop = asyncio.Event()
    task = asyncio.create_task(run_worker(stop)) if get_settings().worker_enabled else None
    try:
        yield
    finally:
        stop.set()
        if task:
            await task

# Все маршруты под /api: Traefik в Dokploy передаёт путь /api/... без strip prefix
app = FastAPI(
    title="Карточка облигации",
    version=settings.app_version,
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(UploadLimitMiddleware, max_bytes=settings.max_upload_bytes * settings.max_upload_files + 1024 * 1024)
if settings.cors_origin_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )
app.include_router(cards_router)
app.include_router(iss_router)
app.include_router(issues_router)
app.include_router(admin_router)


@app.exception_handler(ProcessingError)
async def processing_error(request: Request, exc: ProcessingError):
    return JSONResponse(status_code=exc.http_status, content={"detail": exc.as_dict()})


@app.exception_handler(SQLAlchemyError)
async def database_error(request: Request, exc: SQLAlchemyError):
    return JSONResponse(status_code=503, content={"detail": {"code": "database_unavailable", "message": "База данных недоступна"}})


@app.get("/api/health")
def health() -> dict:
    db_ok = check_db()
    return {
        "status": "ok" if db_ok else "degraded",
        "db": "ok" if db_ok else "error",
        "llm": "configured" if settings.llm_api_key else "not_configured",
        "version": settings.app_version,
    }
