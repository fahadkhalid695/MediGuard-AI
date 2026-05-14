"""
MediGuard AI — Vitals API Router.

Endpoints:
    POST /vitals          — Ingest a new vital reading
    GET  /vitals/latest/{patient_id} — Retrieve cached latest vitals
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status

from app.db.postgres import save_vital_reading
from app.db.redis_client import cache_latest_vitals, get_cached_vitals, publish_vitals
from app.models.vitals import VitalsCreate, VitalsFromCache, VitalsResponse
from app.services.anomaly import detect_anomaly

logger = logging.getLogger("mediguard.routers.vitals")

router = APIRouter(prefix="/vitals", tags=["Vitals"])


@router.post(
    "",
    response_model=VitalsResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record a new vital reading",
    description=(
        "Accepts a JSON payload of vital signs, validates physiological ranges, "
        "persists to PostgreSQL, caches the latest reading in Redis (TTL: 60s), "
        "and publishes to the `vitals:{patient_id}` Pub/Sub channel."
    ),
)
async def create_vital_reading(payload: VitalsCreate) -> VitalsResponse:
    """
    Ingest pipeline:
    1. Pydantic validates field ranges + systolic > diastolic
    2. Anomaly detection flags readings outside clinical norms
    3. INSERT into PostgreSQL `vitals` table
    4. Cache latest reading in Redis Hash (TTL from settings)
    5. Publish to Redis Pub/Sub channel
    """

    vital_id = uuid.uuid4()
    recorded_at = payload.timestamp.replace(tzinfo=timezone.utc) if payload.timestamp.tzinfo is None else payload.timestamp

    # ── Step 1: Anomaly detection ───────────────────────────────────────
    is_anomalous = detect_anomaly(
        heart_rate=payload.heart_rate,
        bp_systolic=payload.bp_systolic,
        bp_diastolic=payload.bp_diastolic,
        spo2=payload.spo2,
        temperature=payload.temperature,
        respiratory_rate=payload.respiratory_rate,
    )

    if is_anomalous:
        logger.warning(
            "⚠ Anomalous reading detected for patient %s: HR=%d, BP=%d/%d, SpO2=%.1f, Temp=%.1f",
            payload.patient_id,
            payload.heart_rate,
            payload.bp_systolic,
            payload.bp_diastolic,
            payload.spo2,
            payload.temperature,
        )

    # ── Step 2: Persist to PostgreSQL ───────────────────────────────────
    try:
        row = await save_vital_reading(
            vital_id=vital_id,
            patient_id=payload.patient_id,
            heart_rate=payload.heart_rate,
            systolic_bp=payload.bp_systolic,
            diastolic_bp=payload.bp_diastolic,
            spo2=payload.spo2,
            temperature=payload.temperature,
            respiratory_rate=payload.respiratory_rate,
            recorded_at=recorded_at,
            is_anomalous=is_anomalous,
        )
    except Exception as exc:
        logger.error("Failed to save vital reading: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {exc}",
        )

    # ── Step 3: Cache in Redis ──────────────────────────────────────────
    cache_payload = {
        "patient_id": payload.patient_id,
        "heart_rate": payload.heart_rate,
        "bp_systolic": payload.bp_systolic,
        "bp_diastolic": payload.bp_diastolic,
        "spo2": payload.spo2,
        "temperature": payload.temperature,
        "respiratory_rate": payload.respiratory_rate,
        "recorded_at": recorded_at,
        "is_anomalous": is_anomalous,
    }

    cached = await cache_latest_vitals(payload.patient_id, cache_payload)

    # ── Step 4: Publish to Pub/Sub ──────────────────────────────────────
    published = await publish_vitals(payload.patient_id, cache_payload)

    # ── Build response ──────────────────────────────────────────────────
    return VitalsResponse(
        id=vital_id,
        patient_id=payload.patient_id,
        heart_rate=payload.heart_rate,
        bp_systolic=payload.bp_systolic,
        bp_diastolic=payload.bp_diastolic,
        spo2=payload.spo2,
        temperature=payload.temperature,
        respiratory_rate=payload.respiratory_rate,
        recorded_at=recorded_at,
        is_anomalous=is_anomalous,
        cached=cached,
        published=published,
    )


@router.get(
    "/latest/{patient_id}",
    response_model=VitalsFromCache,
    summary="Get latest cached vitals",
    description="Retrieves the most recent vital reading from Redis cache. Returns 404 if no cached data exists.",
)
async def get_latest_vitals(patient_id: uuid.UUID) -> VitalsFromCache:
    """Fetch the latest vitals from Redis cache for near-instant response."""

    data = await get_cached_vitals(patient_id)

    if not data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No cached vitals found for patient {patient_id}. The cache may have expired.",
        )

    return VitalsFromCache(
        patient_id=data.get("patient_id", str(patient_id)),
        heart_rate=int(data["heart_rate"]),
        bp_systolic=int(data["bp_systolic"]),
        bp_diastolic=int(data["bp_diastolic"]),
        spo2=float(data["spo2"]),
        temperature=float(data["temperature"]),
        respiratory_rate=int(data["respiratory_rate"]) if data.get("respiratory_rate") else None,
        recorded_at=data["recorded_at"],
        is_anomalous=data.get("is_anomalous", "false").lower() == "true",
    )
