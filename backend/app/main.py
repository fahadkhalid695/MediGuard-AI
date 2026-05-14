"""
MediGuard AI — FastAPI Application Entry Point.

Bootstraps the application with:
- Lifespan management for PostgreSQL and Redis connections
- CORS middleware for frontend integration
- Health check endpoint
- Vitals API router
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db.postgres import check_postgres, close_postgres, init_postgres
from app.db.redis_client import check_redis, close_redis, init_redis
from app.models.vitals import HealthResponse
from app.routers.vitals import router as vitals_router

# ── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG if settings.app_debug else logging.INFO,
    format="%(asctime)s │ %(levelname)-8s │ %(name)s │ %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("mediguard")


# ── Lifespan ────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage startup/shutdown of database connections."""
    logger.info("🚀 Starting MediGuard AI Vitals Service...")

    # Startup
    try:
        await init_postgres()
        logger.info("✅ PostgreSQL connected")
    except Exception as exc:
        logger.error("❌ PostgreSQL connection failed: %s", exc)
        logger.warning("⚠ Service will start but DB operations will fail")

    try:
        await init_redis()
        logger.info("✅ Redis connected")
    except Exception as exc:
        logger.error("❌ Redis connection failed: %s", exc)
        logger.warning("⚠ Service will start but caching/pubsub will fail")

    yield  # Application runs here

    # Shutdown
    logger.info("🛑 Shutting down MediGuard AI Vitals Service...")
    await close_postgres()
    await close_redis()
    logger.info("👋 Shutdown complete")


# ── App Factory ─────────────────────────────────────────────────────────────
app = FastAPI(
    title="MediGuard AI — Vitals Service",
    description=(
        "Real-time vital signs ingestion, validation, and monitoring API. "
        "Accepts vitals from IoT devices and patient monitors, validates "
        "physiological ranges, detects anomalies, persists to PostgreSQL, "
        "and broadcasts via Redis Pub/Sub."
    ),
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── Middleware ──────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routes ──────────────────────────────────────────────────────────────────
app.include_router(vitals_router, prefix="/api/v1")


@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["System"],
    summary="Service health check",
)
async def health_check() -> HealthResponse:
    """Check connectivity to PostgreSQL and Redis."""
    pg_ok = await check_postgres()
    redis_ok = await check_redis()

    return HealthResponse(
        status="healthy" if (pg_ok and redis_ok) else "degraded",
        service="mediguard-vitals",
        postgres="connected" if pg_ok else "disconnected",
        redis="connected" if redis_ok else "disconnected",
    )
