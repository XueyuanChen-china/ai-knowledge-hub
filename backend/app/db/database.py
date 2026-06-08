from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

from app.config import get_settings
from app.db import models  # noqa: F401

settings = get_settings()

connect_args = {"check_same_thread": False}
engine = create_engine(settings.database_url, connect_args=connect_args)


def create_db_and_tables() -> None:
    db_path = settings.database_url.removeprefix("sqlite:///")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
