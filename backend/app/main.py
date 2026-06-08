from fastapi import FastAPI

from app.config import get_settings
from app.db.database import create_db_and_tables

settings = get_settings()

app = FastAPI(title=settings.app_name)


@app.on_event("startup")
def on_startup() -> None:
    create_db_and_tables()


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}
