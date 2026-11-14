from fastapi import FastAPI

from app.cards.router import router as cards_router
from app.config import get_settings
from app.db import check_db
from app.iss.router import router as iss_router
from app.llm.client import LLMClient

settings = get_settings()

app = FastAPI(title="Карточка облигации", version=settings.app_version)
app.include_router(cards_router)
app.include_router(iss_router)


@app.get("/api/health")
def health() -> dict:
    db_ok = check_db()
    return {
        "status": "ok" if db_ok else "degraded",
        "db": "ok" if db_ok else "error",
        "llm": "configured" if LLMClient(settings).configured else "not_configured",
        "version": settings.app_version,
    }
