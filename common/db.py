"""Shared SQLAlchemy engine and session factory.

All agents that read or write Postgres import from here — one engine per
process, not one per agent.  asyncpg (used by Agent 1's HITL queue) and
SQLAlchemy (used by Agent 3+) coexist on the same DATABASE_URL; they open
their own connection pools independently.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from common.config import settings


def get_engine():
    """Return a synchronous SQLAlchemy engine configured from settings.

    Uses the psycopg2 dialect (sync).  For async usage (future agents) swap to
    create_async_engine with asyncpg — for now Agent 3's persistence path is
    synchronous (no LLM awaits, no I/O concurrency needed per-section).
    """
    url = settings.DATABASE_URL
    # asyncpg uses postgresql+asyncpg://; SQLAlchemy sync needs plain postgresql://
    sync_url = url.replace("postgresql+asyncpg://", "postgresql://")
    return create_engine(sync_url, pool_pre_ping=True)


SessionLocal = sessionmaker(bind=get_engine(), autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass
