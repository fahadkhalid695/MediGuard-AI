"""
Tests for the anomaly detection service.

These tests verify that the rule-based anomaly detector correctly
flags readings outside clinical normal ranges.
"""

from __future__ import annotations

import pytest

from app.services.anomaly import CLINICAL_RANGES, detect_anomaly


# ─── Helpers ────────────────────────────────────────────────────────────────

def _normal_vitals(**overrides) -> dict:
    """Return a normal-range vitals dict with optional overrides."""
    base = {
        "heart_rate": 72,
        "bp_systolic": 120,
        "bp_diastolic": 78,
        "spo2": 97.5,
        "temperature": 36.8,
        "respiratory_rate": 16,
    }
    base.update(overrides)
    return base


# ─── Normal Range Tests ─────────────────────────────────────────────────────

class TestNormalReadings:
    """Verify that normal readings are NOT flagged as anomalous."""

    def test_baseline_normal(self):
        assert detect_anomaly(**_normal_vitals()) is False

    def test_normal_without_respiratory_rate(self):
        vitals = _normal_vitals()
        del vitals["respiratory_rate"]
        assert detect_anomaly(**vitals) is False

    def test_normal_edge_low(self):
        """Values at the lower boundary of normal range."""
        assert detect_anomaly(**_normal_vitals(
            heart_rate=50,
            bp_systolic=85,
            bp_diastolic=50,
            spo2=92.0,
            temperature=35.5,
            respiratory_rate=10,
        )) is False

    def test_normal_edge_high(self):
        """Values at the upper boundary of normal range."""
        assert detect_anomaly(**_normal_vitals(
            heart_rate=120,
            bp_systolic=160,
            bp_diastolic=100,
            spo2=100.0,
            temperature=38.5,
            respiratory_rate=25,
        )) is False


# ─── Anomalous Heart Rate ───────────────────────────────────────────────────

class TestAnomalousHeartRate:

    def test_bradycardia(self):
        """HR below 50 bpm should be flagged."""
        assert detect_anomaly(**_normal_vitals(heart_rate=45)) is True

    def test_tachycardia(self):
        """HR above 120 bpm should be flagged."""
        assert detect_anomaly(**_normal_vitals(heart_rate=150)) is True

    def test_severe_bradycardia(self):
        assert detect_anomaly(**_normal_vitals(heart_rate=30)) is True


# ─── Anomalous Blood Pressure ──────────────────────────────────────────────

class TestAnomalousBloodPressure:

    def test_hypotension(self):
        assert detect_anomaly(**_normal_vitals(bp_systolic=75)) is True

    def test_hypertension(self):
        assert detect_anomaly(**_normal_vitals(bp_systolic=180)) is True

    def test_diastolic_too_low(self):
        assert detect_anomaly(**_normal_vitals(bp_diastolic=45)) is True

    def test_diastolic_too_high(self):
        assert detect_anomaly(**_normal_vitals(bp_diastolic=110)) is True


# ─── Anomalous SpO2 ────────────────────────────────────────────────────────

class TestAnomalousSpO2:

    def test_desaturation_mild(self):
        """SpO2 below 92% should be flagged."""
        assert detect_anomaly(**_normal_vitals(spo2=89.0)) is True

    def test_desaturation_severe(self):
        assert detect_anomaly(**_normal_vitals(spo2=78.0)) is True

    def test_borderline_normal(self):
        """SpO2 exactly at 92% is in-range (boundary)."""
        assert detect_anomaly(**_normal_vitals(spo2=92.0)) is False


# ─── Anomalous Temperature ─────────────────────────────────────────────────

class TestAnomalousTemperature:

    def test_fever(self):
        assert detect_anomaly(**_normal_vitals(temperature=39.5)) is True

    def test_hypothermia(self):
        assert detect_anomaly(**_normal_vitals(temperature=34.0)) is True

    def test_high_fever(self):
        assert detect_anomaly(**_normal_vitals(temperature=41.0)) is True


# ─── Anomalous Respiratory Rate ────────────────────────────────────────────

class TestAnomalousRespiratoryRate:

    def test_tachypnea(self):
        assert detect_anomaly(**_normal_vitals(respiratory_rate=30)) is True

    def test_bradypnea(self):
        assert detect_anomaly(**_normal_vitals(respiratory_rate=7)) is True


# ─── Multiple Anomalies ────────────────────────────────────────────────────

class TestMultipleAnomalies:

    def test_multiple_flags_still_returns_true(self):
        """Even with multiple out-of-range values, result is a single True."""
        assert detect_anomaly(**_normal_vitals(
            heart_rate=180,
            spo2=78.0,
            temperature=40.5,
        )) is True
