"""
MediGuard AI — Redis caching & Pub/Sub for real-time vitals.

Handles three concerns:
1. Cache the latest vital reading per patient (Hash with TTL)
2. Publish each reading to a Pub/Sub channel for WebSocket consumers
3. Health-check connectivity
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Optional

import redis.asyncio as aioredis

from app.config import settings

logger = logging.getLogger("mediguard.db.redis")

# ── Module-level Redis clients ──────────────────────────────────────────────
_redis: aioredis.Redis | None = None


async def init_redis() -> None:
    """Create the async Redis connection pool. Called once during app lifespan."""
    global _redis
    _redis = aioredis.from_url(
        settings.redis_url,
        decode_responses=True,
        max_connections=50,
    )
    logger.info("Redis connection pool created (%s:%s)", settings.redis_host, settings.redis_port)


async def close_redis() -> None:
    """Close the Redis connection pool. Called during app shutdown."""
    global _redis
    if _redis:
        await _redis.aclose()
        _redis = None
        logger.info("Redis connection pool closed")


async def get_redis() -> aioredis.Redis:
    """Return the active Redis client, raising if not initialized."""
    if _redis is None:
        raise RuntimeError("Redis client not initialized. Call init_redis() first.")
    return _redis


async def check_redis() -> bool:
    """Health-check: ping Redis."""
    try:
        client = await get_redis()
        return await client.ping()
    except Exception as exc:
        logger.warning("Redis health check failed: %s", exc)
        return False


# ─── Cache Operations ───────────────────────────────────────────────────────

def _cache_key(patient_id: uuid.UUID | str) -> str:
    """Build the Redis key for a patient's latest vitals."""
    return f"mediguard:vitals:latest:{patient_id}"


def _pubsub_channel(patient_id: uuid.UUID | str) -> str:
    """Build the Pub/Sub channel name for a patient's vitals stream."""
    return f"vitals:{patient_id}"


def _serialize_reading(data: dict[str, Any]) -> dict[str, str]:
    """Convert a vitals dict to Redis-compatible string values."""
    serialized = {}
    for key, value in data.items():
        if isinstance(value, (uuid.UUID, datetime)):
            serialized[key] = str(value)
        elif isinstance(value, bool):
            serialized[key] = "true" if value else "false"
        elif value is None:
            serialized[key] = ""
        else:
            serialized[key] = str(value)
    return serialized


async def cache_latest_vitals(
    patient_id: uuid.UUID,
    reading: dict[str, Any],
    ttl: int | None = None,
) -> bool:
    """
    Cache the latest vital reading for a patient as a Redis Hash.

    Args:
        patient_id: The patient's UUID.
        reading: Dict of vital sign values.
        ttl: Time-to-live in seconds. Defaults to settings.vitals_cache_ttl.

    Returns:
        True if caching succeeded, False otherwise.
    """
    try:
        client = await get_redis()
        key = _cache_key(patient_id)
        ttl = ttl or settings.vitals_cache_ttl

        # Store as hash and set expiry atomically via pipeline
        async with client.pipeline(transaction=True) as pipe:
            pipe.delete(key)
            pipe.hset(key, mapping=_serialize_reading(reading))
            pipe.expire(key, ttl)
            await pipe.execute()

        logger.debug("Cached latest vitals for patient %s (TTL=%ds)", patient_id, ttl)
        return True

    except Exception as exc:
        logger.error("Failed to cache vitals for patient %s: %s", patient_id, exc)
        return False


async def get_cached_vitals(patient_id: uuid.UUID | str) -> Optional[dict[str, str]]:
    """
    Retrieve the latest cached vitals for a patient.

    Returns None if no cached data exists or the key has expired.
    """
    try:
        client = await get_redis()
        data = await client.hgetall(_cache_key(patient_id))
        return data if data else None
    except Exception as exc:
        logger.error("Failed to get cached vitals for patient %s: %s", patient_id, exc)
        return None


async def publish_vitals(
    patient_id: uuid.UUID,
    reading: dict[str, Any],
) -> bool:
    """
    Publish a vital reading to the Redis Pub/Sub channel `vitals:{patient_id}`.

    The message is JSON-serialized for easy consumption by WebSocket handlers.

    Returns:
        True if publishing succeeded, False otherwise.
    """
    try:
        client = await get_redis()
        channel = _pubsub_channel(patient_id)

        # Build a JSON-safe payload
        payload = _serialize_reading(reading)
        message = json.dumps(payload)

        subscribers = await client.publish(channel, message)
        logger.debug(
            "Published vitals to channel '%s' (%d subscribers)",
            channel, subscribers,
        )
        return True

    except Exception as exc:
        logger.error("Failed to publish vitals for patient %s: %s", patient_id, exc)
        return False
