"""
MediGuard AI — Anomaly detection for vital signs.

A rule-based anomaly detector that flags readings falling outside
clinically normal ranges. This runs on every incoming vital reading
before persistence.

Note: This is a first-pass implementation. In production, this would
be replaced or augmented with ML-based anomaly detection trained on
patient-specific baselines.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("mediguard.services.anomaly")

# ─── Clinical Normal Ranges ─────────────────────────────────────────────────
# These are tighter than the API validation ranges (which allow
# physiologically *possible* values). These represent values that
# should trigger an anomaly flag for further review.

CLINICAL_RANGES = {
    "heart_rate": {
        "min": 50,
        "max": 120,
        "unit": "bpm",
    },
    "bp_systolic": {
        "min": 85,
        "max": 160,
        "unit": "mmHg",
    },
    "bp_diastolic": {
        "min": 50,
        "max": 100,
        "unit": "mmHg",
    },
    "spo2": {
        "min": 92.0,
        "max": 100.0,
        "unit": "%",
    },
    "temperature": {
        "min": 35.5,
        "max": 38.5,
        "unit": "°C",
    },
    "respiratory_rate": {
        "min": 10,
        "max": 25,
        "unit": "breaths/min",
    },
}


def detect_anomaly(
    *,
    heart_rate: int,
    bp_systolic: int,
    bp_diastolic: int,
    spo2: float,
    temperature: float,
    respiratory_rate: Optional[int] = None,
) -> bool:
    """
    Check vital signs against clinical normal ranges.

    Returns True if ANY vital sign falls outside its normal range.
    """
    checks = {
        "heart_rate": heart_rate,
        "bp_systolic": bp_systolic,
        "bp_diastolic": bp_diastolic,
        "spo2": spo2,
        "temperature": temperature,
    }

    if respiratory_rate is not None:
        checks["respiratory_rate"] = respiratory_rate

    anomalies: list[str] = []

    for vital_name, value in checks.items():
        bounds = CLINICAL_RANGES[vital_name]
        if value < bounds["min"] or value > bounds["max"]:
            anomalies.append(
                f"{vital_name}={value}{bounds['unit']} "
                f"(normal: {bounds['min']}–{bounds['max']})"
            )

    if anomalies:
        logger.info("Anomalies detected: %s", "; ".join(anomalies))
        return True

    return False
