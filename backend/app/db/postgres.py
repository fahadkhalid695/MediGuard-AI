"""
MediGuard AI — Async PostgreSQL connection pool & vitals persistence.

Uses SQLAlchemy's async engine backed by asyncpg for high-throughput writes.
The module exposes lifecycle helpers (init/close) and a `save_vital_reading`
function that inserts a row into the `vitals` table.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import settings

logger = logging.getLogger("mediguard.db.postgres")

# ── Module-level engine (initialized at app startup) ────────────────────────
_engine: AsyncEngine | None = None


async def init_postgres() -> None:
    """Create the async connection pool. Called once during app lifespan."""
    global _engine
    _engine = create_async_engine(
        settings.postgres_dsn,
        pool_size=20,
        max_overflow=10,
        pool_pre_ping=True,
        echo=settings.app_debug,
    )
    logger.info("PostgreSQL connection pool created (%s)", settings.postgres_host)


async def close_postgres() -> None:
    """Dispose the connection pool. Called during app shutdown."""
    global _engine
    if _engine:
        await _engine.dispose()
        _engine = None
        logger.info("PostgreSQL connection pool closed")


async def get_engine() -> AsyncEngine:
    """Return the active engine, raising if not initialized."""
    if _engine is None:
        raise RuntimeError("PostgreSQL engine not initialized. Call init_postgres() first.")
    return _engine


async def check_postgres() -> bool:
    """Health-check: execute a trivial query."""
    try:
        engine = await get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.warning("PostgreSQL health check failed: %s", exc)
        return False


async def save_vital_reading(
    *,
    vital_id: uuid.UUID,
    patient_id: uuid.UUID,
    heart_rate: int,
    systolic_bp: int,
    diastolic_bp: int,
    spo2: float,
    temperature: float,
    respiratory_rate: int | None,
    recorded_at: datetime,
    is_anomalous: bool,
    source: str = "monitor",
) -> dict[str, Any]:
    """
    Insert a single vital reading into the `vitals` table.

    Returns a dict of the inserted row for response construction.
    """
    engine = await get_engine()

    query = text("""
        INSERT INTO vitals (
            id, patient_id, recorded_at,
            heart_rate, systolic_bp, diastolic_bp,
            spo2, temperature, respiratory_rate,
            source, is_anomalous
        ) VALUES (
            :id, :patient_id, :recorded_at,
            :heart_rate, :systolic_bp, :diastolic_bp,
            :spo2, :temperature, :respiratory_rate,
            :source, :is_anomalous
        )
        RETURNING id, patient_id, recorded_at,
                  heart_rate, systolic_bp, diastolic_bp,
                  spo2, temperature, respiratory_rate,
                  is_anomalous
    """)

    params = {
        "id": vital_id,
        "patient_id": patient_id,
        "recorded_at": recorded_at,
        "heart_rate": heart_rate,
        "systolic_bp": systolic_bp,
        "diastolic_bp": diastolic_bp,
        "spo2": spo2,
        "temperature": temperature,
        "respiratory_rate": respiratory_rate,
        "source": source,
        "is_anomalous": is_anomalous,
    }

    async with engine.begin() as conn:
        result = await conn.execute(query, params)
        row = result.mappings().one()

    logger.info(
        "Saved vital reading %s for patient %s (anomalous=%s)",
        vital_id, patient_id, is_anomalous,
    )

    return dict(row)
