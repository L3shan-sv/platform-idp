"""
Nerve IDP Gateway — Database

ALL app connections go through PgBouncer (port 6432).
NullPool is correct here — PgBouncer manages the actual pool.
SQLAlchemy's own pool on top of PgBouncer = double-pooling = connection exhaustion.
"""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool
from typing import AsyncGenerator
import time

from app.core.config import settings

engine = create_async_engine(settings.DATABASE_URL, pool_class=NullPool, pool_pre_ping=True)
async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def check_db_health() -> dict:
    start = time.monotonic()
    try:
        async with async_session_maker() as session:
            await session.execute("SELECT 1")
        return {"healthy": True, "latency_ms": int((time.monotonic() - start) * 1000)}
    except Exception as exc:
        return {"healthy": False, "latency_ms": -1, "error": str(exc)}
