"""
MediGuard AI — Pydantic models for vitals data.

Defines request/response schemas with field-level validation
to reject physiologically impossible readings at the API boundary.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ─── Validation Ranges ──────────────────────────────────────────────────────
# These represent the widest physiologically *possible* ranges,
# not "normal" ranges. Alert thresholds (in the DB) handle clinical norms.

VITAL_RANGES = {
    "heart_rate":       (20, 300),      # bpm
    "bp_systolic":      (40, 300),      # mmHg
    "bp_diastolic":     (20, 200),      # mmHg
    "spo2":             (30.0, 100.0),  # percentage
    "temperature":      (25.0, 45.0),   # °C
    "respiratory_rate": (4, 60),        # breaths/min
}


class VitalsCreate(BaseModel):
    """
    Incoming vitals payload from monitoring devices.

    All vital sign fields are required. The `timestamp` field
    defaults to the current UTC time if not provided.
    """

    patient_id: uuid.UUID = Field(
        ...,
        description="UUID of the patient being monitored",
        examples=["a1b2c3d4-e5f6-7890-abcd-ef1234567890"],
    )

    heart_rate: int = Field(
        ...,
        ge=VITAL_RANGES["heart_rate"][0],
        le=VITAL_RANGES["heart_rate"][1],
        description="Heart rate in beats per minute (20–300 bpm)",
        examples=[72],
    )

    bp_systolic: int = Field(
        ...,
        ge=VITAL_RANGES["bp_systolic"][0],
        le=VITAL_RANGES["bp_systolic"][1],
        description="Systolic blood pressure in mmHg (40–300)",
        examples=[120],
    )

    bp_diastolic: int = Field(
        ...,
        ge=VITAL_RANGES["bp_diastolic"][0],
        le=VITAL_RANGES["bp_diastolic"][1],
        description="Diastolic blood pressure in mmHg (20–200)",
        examples=[80],
    )

    spo2: float = Field(
        ...,
        ge=VITAL_RANGES["spo2"][0],
        le=VITAL_RANGES["spo2"][1],
        description="Blood oxygen saturation percentage (30–100%)",
        examples=[98.5],
    )

    temperature: float = Field(
        ...,
        ge=VITAL_RANGES["temperature"][0],
        le=VITAL_RANGES["temperature"][1],
        description="Body temperature in °C (25.0–45.0)",
        examples=[36.8],
    )

    respiratory_rate: Optional[int] = Field(
        default=None,
        ge=VITAL_RANGES["respiratory_rate"][0],
        le=VITAL_RANGES["respiratory_rate"][1],
        description="Respiratory rate in breaths/min (4–60). Optional.",
        examples=[16],
    )

    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Reading timestamp in ISO 8601 format. Defaults to current UTC time.",
        examples=["2026-05-14T18:30:00Z"],
    )

    @model_validator(mode="after")
    def validate_blood_pressure_consistency(self) -> "VitalsCreate":
        """Systolic must always exceed diastolic."""
        if self.bp_systolic <= self.bp_diastolic:
            raise ValueError(
                f"Systolic BP ({self.bp_systolic}) must be greater than "
                f"diastolic BP ({self.bp_diastolic})"
            )
        return self


class VitalsResponse(BaseModel):
    """Response returned after successfully recording vitals."""

    id: uuid.UUID
    patient_id: uuid.UUID
    heart_rate: int
    bp_systolic: int
    bp_diastolic: int
    spo2: float
    temperature: float
    respiratory_rate: Optional[int] = None
    recorded_at: datetime
    is_anomalous: bool
    cached: bool = Field(
        default=False,
        description="Whether the reading was successfully cached in Redis",
    )
    published: bool = Field(
        default=False,
        description="Whether the reading was published to the Pub/Sub channel",
    )

    model_config = {"from_attributes": True}


class VitalsFromCache(BaseModel):
    """Shape of a cached vitals reading retrieved from Redis."""

    patient_id: str
    heart_rate: int
    bp_systolic: int
    bp_diastolic: int
    spo2: float
    temperature: float
    respiratory_rate: Optional[int] = None
    recorded_at: str
    is_anomalous: bool


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = "ok"
    service: str = "mediguard-vitals"
    postgres: str = "unknown"
    redis: str = "unknown"
    version: str = "0.1.0"
