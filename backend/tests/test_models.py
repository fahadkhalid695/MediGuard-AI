"""
Tests for Pydantic vitals models — validation ranges and cross-field rules.

These tests do NOT require PostgreSQL or Redis; they validate the
Pydantic schema layer in isolation.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models.vitals import VITAL_RANGES, VitalsCreate


# ─── Helpers ────────────────────────────────────────────────────────────────

VALID_PATIENT_ID = str(uuid.uuid4())

def _base_payload(**overrides) -> dict:
    """Return a valid baseline payload, with optional overrides."""
    data = {
        "patient_id": VALID_PATIENT_ID,
        "heart_rate": 72,
        "bp_systolic": 120,
        "bp_diastolic": 80,
        "spo2": 98.0,
        "temperature": 36.8,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    data.update(overrides)
    return data


# ─── Happy Path ─────────────────────────────────────────────────────────────

class TestVitalsCreateValid:
    """Tests for valid vitals payloads that should pass validation."""

    def test_valid_baseline(self):
        """Normal vitals should parse without errors."""
        v = VitalsCreate(**_base_payload())
        assert v.heart_rate == 72
        assert v.bp_systolic == 120
        assert v.bp_diastolic == 80
        assert v.spo2 == 98.0
        assert v.temperature == 36.8

    def test_valid_with_respiratory_rate(self):
        """Respiratory rate is optional but should be accepted when present."""
        v = VitalsCreate(**_base_payload(respiratory_rate=16))
        assert v.respiratory_rate == 16

    def test_valid_without_respiratory_rate(self):
        """Missing respiratory_rate should default to None."""
        v = VitalsCreate(**_base_payload())
        assert v.respiratory_rate is None

    def test_valid_edge_low(self):
        """Minimum boundary values should be accepted."""
        v = VitalsCreate(**_base_payload(
            heart_rate=20,
            bp_systolic=41,
            bp_diastolic=20,
            spo2=30.0,
            temperature=25.0,
        ))
        assert v.heart_rate == 20

    def test_valid_edge_high(self):
        """Maximum boundary values should be accepted."""
        v = VitalsCreate(**_base_payload(
            heart_rate=300,
            bp_systolic=300,
            bp_diastolic=200,
            spo2=100.0,
            temperature=45.0,
        ))
        assert v.heart_rate == 300

    def test_timestamp_defaults_to_now(self):
        """Omitting timestamp should auto-fill with current UTC time."""
        payload = _base_payload()
        del payload["timestamp"]
        v = VitalsCreate(**payload)
        assert v.timestamp is not None
        assert isinstance(v.timestamp, datetime)

    def test_patient_id_parsed_as_uuid(self):
        """patient_id string should be parsed into a UUID object."""
        v = VitalsCreate(**_base_payload())
        assert isinstance(v.patient_id, uuid.UUID)


# ─── Range Validation ───────────────────────────────────────────────────────

class TestVitalsCreateRangeValidation:
    """Tests that out-of-range values are rejected."""

    def test_heart_rate_too_low(self):
        with pytest.raises(ValidationError, match="heart_rate"):
            VitalsCreate(**_base_payload(heart_rate=19))

    def test_heart_rate_too_high(self):
        with pytest.raises(ValidationError, match="heart_rate"):
            VitalsCreate(**_base_payload(heart_rate=301))

    def test_bp_systolic_too_low(self):
        with pytest.raises(ValidationError, match="bp_systolic"):
            VitalsCreate(**_base_payload(bp_systolic=39))

    def test_bp_systolic_too_high(self):
        with pytest.raises(ValidationError, match="bp_systolic"):
            VitalsCreate(**_base_payload(bp_systolic=301))

    def test_bp_diastolic_too_low(self):
        with pytest.raises(ValidationError, match="bp_diastolic"):
            VitalsCreate(**_base_payload(bp_diastolic=19))

    def test_bp_diastolic_too_high(self):
        with pytest.raises(ValidationError, match="bp_diastolic"):
            VitalsCreate(**_base_payload(bp_diastolic=201))

    def test_spo2_too_low(self):
        with pytest.raises(ValidationError, match="spo2"):
            VitalsCreate(**_base_payload(spo2=29.9))

    def test_spo2_too_high(self):
        with pytest.raises(ValidationError, match="spo2"):
            VitalsCreate(**_base_payload(spo2=100.1))

    def test_temperature_too_low(self):
        with pytest.raises(ValidationError, match="temperature"):
            VitalsCreate(**_base_payload(temperature=24.9))

    def test_temperature_too_high(self):
        with pytest.raises(ValidationError, match="temperature"):
            VitalsCreate(**_base_payload(temperature=45.1))

    def test_respiratory_rate_too_low(self):
        with pytest.raises(ValidationError, match="respiratory_rate"):
            VitalsCreate(**_base_payload(respiratory_rate=3))

    def test_respiratory_rate_too_high(self):
        with pytest.raises(ValidationError, match="respiratory_rate"):
            VitalsCreate(**_base_payload(respiratory_rate=61))


# ─── Cross-Field Validation ─────────────────────────────────────────────────

class TestVitalsCreateCrossField:
    """Tests for cross-field validation rules."""

    def test_systolic_must_exceed_diastolic(self):
        """Systolic == diastolic should fail."""
        with pytest.raises(ValidationError, match="Systolic BP"):
            VitalsCreate(**_base_payload(bp_systolic=80, bp_diastolic=80))

    def test_systolic_less_than_diastolic(self):
        """Systolic < diastolic should fail."""
        with pytest.raises(ValidationError, match="Systolic BP"):
            VitalsCreate(**_base_payload(bp_systolic=70, bp_diastolic=90))

    def test_systolic_barely_above_diastolic(self):
        """Systolic just 1 above diastolic should pass (edge case)."""
        v = VitalsCreate(**_base_payload(bp_systolic=81, bp_diastolic=80))
        assert v.bp_systolic == 81


# ─── Type Coercion / Missing Fields ────────────────────────────────────────

class TestVitalsCreateTypeErrors:
    """Tests that missing required fields and wrong types are rejected."""

    def test_missing_patient_id(self):
        payload = _base_payload()
        del payload["patient_id"]
        with pytest.raises(ValidationError, match="patient_id"):
            VitalsCreate(**payload)

    def test_missing_heart_rate(self):
        payload = _base_payload()
        del payload["heart_rate"]
        with pytest.raises(ValidationError, match="heart_rate"):
            VitalsCreate(**payload)

    def test_invalid_patient_id_format(self):
        with pytest.raises(ValidationError, match="patient_id"):
            VitalsCreate(**_base_payload(patient_id="not-a-uuid"))

    def test_string_heart_rate_coercion(self):
        """Pydantic should coerce numeric strings to int."""
        v = VitalsCreate(**_base_payload(heart_rate="72"))
        assert v.heart_rate == 72
