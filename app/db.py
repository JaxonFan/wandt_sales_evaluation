"""SQLAlchemy engine + session. SQLite for dev, Postgres (RDS) for prod — same models."""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from .config import DATABASE_URL, DATA_DIR

os.makedirs(DATA_DIR, exist_ok=True)

# SQLite needs check_same_thread off for the threadpool; Postgres ignores it.
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
