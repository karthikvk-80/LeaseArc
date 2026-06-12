"""SQLAlchemy engine/session setup for the Postgres-backed auth tables.

DATABASE_URL is read from the environment (see backend/.env). Connects as the
`postgres` superuser/table-owner for local dev, which bypasses RLS (table
owners are exempt unless FORCE ROW LEVEL SECURITY is set, which schema.sql
does not set).
"""

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL")

engine = create_engine(DATABASE_URL, pool_pre_ping=True) if DATABASE_URL else None

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
