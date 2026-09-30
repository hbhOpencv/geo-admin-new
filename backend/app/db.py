# -*- coding: utf-8 -*-
"""
DB dependency wrapper around app.models.get_session()

- Uses SQLite by default (backend/geology_norm.db)
- Enables foreign_keys pragma for SQLite
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import event
from sqlalchemy.engine import Engine

from .models import get_engine, get_session

_engine: Engine | None = None


def get_db_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = get_engine()
    return _engine


# Enable SQLite foreign keys
@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()
    except Exception:
        # Non-sqlite or unsupported driver
        pass


def get_db():
    db = get_session()
    try:
        yield db
    finally:
        db.close()
