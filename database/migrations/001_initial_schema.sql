-- ============================================================================
-- MediGuard AI — Initial Database Schema
-- Migration: 001_initial_schema.sql
-- Created:   2026-05-14
-- Engine:    PostgreSQL 16+
-- ============================================================================

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";      -- UUID generation
CREATE EXTENSION IF NOT EXISTS "pgcrypto";        -- Encryption helpers
CREATE EXTENSION IF NOT EXISTS "timescaledb";     -- Time-series hypertable support (optional)

-- ============================================================================
-- ENUM TYPES
-- ============================================================================

CREATE TYPE gender_enum AS ENUM ('male', 'female', 'other', 'prefer_not_to_say');

CREATE TYPE blood_group_enum AS ENUM (
    'A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-'
);

CREATE TYPE alert_severity_enum AS ENUM ('low', 'medium', 'high', 'critical');

CREATE TYPE alert_status_enum AS ENUM ('active', 'acknowledged', 'resolved', 'dismissed');

CREATE TYPE staff_role_enum AS ENUM ('doctor', 'nurse', 'caregiver', 'specialist');

CREATE TYPE assignment_status_enum AS ENUM ('active', 'inactive', 'transferred');

CREATE TYPE vital_source_enum AS ENUM ('monitor', 'manual', 'wearable', 'iot_device');

-- ============================================================================
-- 1. PATIENTS
-- ============================================================================

CREATE TABLE patients (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    first_name      VARCHAR(100)    NOT NULL,
    last_name       VARCHAR(100)    NOT NULL,
    date_of_birth   DATE            NOT NULL,
    gender          gender_enum     NOT NULL,
    blood_group     blood_group_enum,
    phone           VARCHAR(20),
    email           VARCHAR(255),
    emergency_contact_name  VARCHAR(200),
    emergency_contact_phone VARCHAR(20),
    address         TEXT,
    avatar_url      TEXT,
    is_active       BOOLEAN         NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- Computed column helper: age is derived from date_of_birth at query time
-- Usage:  SELECT *, EXTRACT(YEAR FROM age(date_of_birth)) AS age FROM patients;

CREATE INDEX idx_patients_name       ON patients (last_name, first_name);
CREATE INDEX idx_patients_active     ON patients (is_active) WHERE is_active = TRUE;
CREATE INDEX idx_patients_created    ON patients (created_at DESC);

-- ============================================================================
-- 2. MEDICAL HISTORY
-- ============================================================================

CREATE TABLE medical_history (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    patient_id      UUID            NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    condition_name  VARCHAR(300)    NOT NULL,
    diagnosed_date  DATE,
    resolved_date   DATE,
    is_chronic      BOOLEAN         NOT NULL DEFAULT FALSE,
    notes           TEXT,
    icd_code        VARCHAR(20),           -- ICD-10/11 code
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_medical_history_patient   ON medical_history (patient_id);
CREATE INDEX idx_medical_history_chronic   ON medical_history (patient_id, is_chronic) WHERE is_chronic = TRUE;
CREATE INDEX idx_medical_history_icd       ON medical_history (icd_code);

-- ============================================================================
-- 3. CONDITIONS (active / current)
-- ============================================================================

CREATE TABLE patient_conditions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    patient_id      UUID            NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    condition_name  VARCHAR(300)    NOT NULL,
    severity        alert_severity_enum NOT NULL DEFAULT 'low',
    onset_date      DATE,
    is_active       BOOLEAN         NOT NULL DEFAULT TRUE,
    notes           TEXT,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_conditions_patient        ON patient_conditions (patient_id);
CREATE INDEX idx_conditions_active         ON patient_conditions (patient_id, is_active) WHERE is_active = TRUE;

-- ============================================================================
-- 4. MEDICAL STAFF (Doctors, Caregivers, Nurses, Specialists)
-- ============================================================================

CREATE TABLE medical_staff (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    first_name      VARCHAR(100)    NOT NULL,
    last_name       VARCHAR(100)    NOT NULL,
    role            staff_role_enum NOT NULL,
    specialization  VARCHAR(200),
    license_number  VARCHAR(100),
    phone           VARCHAR(20),
    email           VARCHAR(255)    UNIQUE NOT NULL,
    department      VARCHAR(200),
    avatar_url      TEXT,
    is_active       BOOLEAN         NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_staff_role              ON medical_staff (role);
CREATE INDEX idx_staff_active            ON medical_staff (is_active) WHERE is_active = TRUE;
CREATE INDEX idx_staff_email             ON medical_staff (email);

-- ============================================================================
-- 5. MEDICATIONS
-- ============================================================================

CREATE TABLE medications (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    patient_id      UUID            NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    drug_name       VARCHAR(300)    NOT NULL,
    dosage          VARCHAR(100)    NOT NULL,     -- e.g. "500mg"
    frequency       VARCHAR(100)    NOT NULL,     -- e.g. "twice daily"
    route           VARCHAR(50)     DEFAULT 'oral',  -- oral, IV, topical, etc.
    prescribed_by   UUID            REFERENCES medical_staff(id) ON DELETE SET NULL,
    start_date      DATE            NOT NULL,
    end_date        DATE,
    is_active       BOOLEAN         NOT NULL DEFAULT TRUE,
    side_effects    TEXT,
    notes           TEXT,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_medications_patient       ON medications (patient_id);
CREATE INDEX idx_medications_active        ON medications (patient_id, is_active) WHERE is_active = TRUE;
CREATE INDEX idx_medications_drug          ON medications (drug_name);

-- ============================================================================
-- 6. PATIENT–STAFF ASSIGNMENTS
-- ============================================================================

CREATE TABLE patient_assignments (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    patient_id      UUID                NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    staff_id        UUID                NOT NULL REFERENCES medical_staff(id) ON DELETE CASCADE,
    role            staff_role_enum     NOT NULL,
    status          assignment_status_enum NOT NULL DEFAULT 'active',
    assigned_at     TIMESTAMPTZ         NOT NULL DEFAULT NOW(),
    unassigned_at   TIMESTAMPTZ,
    notes           TEXT,

    -- Prevent duplicate active assignments of the same staff to the same patient
    CONSTRAINT uq_active_assignment UNIQUE (patient_id, staff_id, status)
);

CREATE INDEX idx_assignments_patient     ON patient_assignments (patient_id);
CREATE INDEX idx_assignments_staff       ON patient_assignments (staff_id);
CREATE INDEX idx_assignments_active      ON patient_assignments (patient_id, status) WHERE status = 'active';

-- ============================================================================
-- 7. VITALS — Real-Time + History  (Time-Series Optimized)
-- ============================================================================

CREATE TABLE vitals (
    id              UUID            NOT NULL DEFAULT uuid_generate_v4(),
    patient_id      UUID            NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    recorded_at     TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    -- Core vital signs
    heart_rate          SMALLINT,           -- bpm  (40–220)
    systolic_bp         SMALLINT,           -- mmHg (60–300)
    diastolic_bp        SMALLINT,           -- mmHg (30–200)
    spo2                DECIMAL(5,2),       -- %    (0–100)
    temperature         DECIMAL(4,1),       -- °C   (30.0–45.0)
    respiratory_rate    SMALLINT,           -- breaths/min (5–60)

    -- Extended vitals (optional)
    blood_glucose       DECIMAL(6,2),       -- mg/dL
    pain_level          SMALLINT,           -- 0–10 scale

    -- Metadata
    source              vital_source_enum   DEFAULT 'monitor',
    device_id           VARCHAR(100),
    notes               TEXT,
    is_anomalous        BOOLEAN             DEFAULT FALSE,

    -- Composite primary key for TimescaleDB hypertable
    PRIMARY KEY (id, recorded_at)
);

-- ── Time-series indexes for fast range queries ──────────────────────────────

-- Primary time-based lookup: "Give me vitals for patient X in the last N hours"
CREATE INDEX idx_vitals_patient_time     ON vitals (patient_id, recorded_at DESC);

-- Anomaly scanning: "Show me all anomalous readings in the last day"
CREATE INDEX idx_vitals_anomalous        ON vitals (recorded_at DESC, patient_id)
    WHERE is_anomalous = TRUE;

-- Source filtering: "All readings from wearable devices"
CREATE INDEX idx_vitals_source           ON vitals (source, recorded_at DESC);

-- Covering index for dashboard queries (avoids heap fetches)
CREATE INDEX idx_vitals_dashboard ON vitals (
    patient_id,
    recorded_at DESC
) INCLUDE (heart_rate, systolic_bp, diastolic_bp, spo2, temperature, respiratory_rate);

-- ── Convert to TimescaleDB hypertable (if extension is available) ───────────
-- This partitions the table by recorded_at into time-based chunks for massive
-- performance gains on time-range queries and automatic data retention.

SELECT create_hypertable(
    'vitals',
    'recorded_at',
    partitioning_column => 'patient_id',
    number_partitions   => 4,
    if_not_exists       => TRUE
);

-- Retention policy: automatically drop vitals older than 2 years
SELECT add_retention_policy('vitals', INTERVAL '2 years', if_not_exists => TRUE);

-- Continuous aggregate: hourly averages for trend analysis
CREATE MATERIALIZED VIEW vitals_hourly
WITH (timescaledb.continuous) AS
SELECT
    patient_id,
    time_bucket('1 hour', recorded_at)  AS bucket,
    AVG(heart_rate)::SMALLINT           AS avg_heart_rate,
    AVG(systolic_bp)::SMALLINT          AS avg_systolic_bp,
    AVG(diastolic_bp)::SMALLINT         AS avg_diastolic_bp,
    AVG(spo2)::DECIMAL(5,2)            AS avg_spo2,
    AVG(temperature)::DECIMAL(4,1)     AS avg_temperature,
    AVG(respiratory_rate)::SMALLINT    AS avg_respiratory_rate,
    COUNT(*)                            AS reading_count
FROM vitals
GROUP BY patient_id, bucket
WITH NO DATA;

-- Refresh hourly aggregates every 30 minutes
SELECT add_continuous_aggregate_policy('vitals_hourly',
    start_offset    => INTERVAL '3 hours',
    end_offset      => INTERVAL '1 hour',
    schedule_interval => INTERVAL '30 minutes',
    if_not_exists   => TRUE
);

-- ============================================================================
-- 8. ALERT LOGS
-- ============================================================================

CREATE TABLE alert_logs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    patient_id      UUID                NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    vital_id        UUID,               -- optional link to the triggering vital reading
    severity        alert_severity_enum NOT NULL,
    status          alert_status_enum   NOT NULL DEFAULT 'active',
    title           VARCHAR(300)        NOT NULL,
    message         TEXT                NOT NULL,
    vital_type      VARCHAR(50),        -- e.g. 'heart_rate', 'spo2', 'temperature'
    vital_value     DECIMAL(10,2),      -- the value that triggered the alert
    threshold_min   DECIMAL(10,2),      -- expected min
    threshold_max   DECIMAL(10,2),      -- expected max
    acknowledged_by UUID                REFERENCES medical_staff(id) ON DELETE SET NULL,
    acknowledged_at TIMESTAMPTZ,
    resolved_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ         NOT NULL DEFAULT NOW()
);

-- Fast lookups for active alerts per patient
CREATE INDEX idx_alerts_patient_active   ON alert_logs (patient_id, created_at DESC)
    WHERE status = 'active';

-- Severity-based queries: "Show all critical alerts in the last hour"
CREATE INDEX idx_alerts_severity_time    ON alert_logs (severity, created_at DESC);

-- Staff workload: "Alerts acknowledged by a specific doctor"
CREATE INDEX idx_alerts_acknowledged     ON alert_logs (acknowledged_by, acknowledged_at DESC)
    WHERE acknowledged_by IS NOT NULL;

-- General time-based index
CREATE INDEX idx_alerts_created          ON alert_logs (created_at DESC);

-- Status filtering
CREATE INDEX idx_alerts_status           ON alert_logs (status, created_at DESC);

-- ============================================================================
-- 9. ALERT THRESHOLDS (Configurable per patient)
-- ============================================================================

CREATE TABLE alert_thresholds (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    patient_id      UUID            NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    vital_type      VARCHAR(50)     NOT NULL,   -- 'heart_rate', 'spo2', etc.
    severity        alert_severity_enum NOT NULL,
    min_value       DECIMAL(10,2),
    max_value       DECIMAL(10,2),
    is_active       BOOLEAN         NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_threshold UNIQUE (patient_id, vital_type, severity)
);

CREATE INDEX idx_thresholds_patient ON alert_thresholds (patient_id)
    WHERE is_active = TRUE;

-- ============================================================================
-- 10. AUDIT LOG (Who changed what, when)
-- ============================================================================

CREATE TABLE audit_log (
    id              BIGSERIAL PRIMARY KEY,
    table_name      VARCHAR(100)    NOT NULL,
    record_id       UUID            NOT NULL,
    action          VARCHAR(10)     NOT NULL,   -- INSERT, UPDATE, DELETE
    old_data        JSONB,
    new_data        JSONB,
    performed_by    UUID,
    performed_at    TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_audit_table_record  ON audit_log (table_name, record_id);
CREATE INDEX idx_audit_time          ON audit_log (performed_at DESC);

-- ============================================================================
-- TRIGGERS — Auto-update `updated_at` timestamps
-- ============================================================================

CREATE OR REPLACE FUNCTION trigger_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER set_updated_at BEFORE UPDATE ON patients
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

CREATE TRIGGER set_updated_at BEFORE UPDATE ON patient_conditions
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

CREATE TRIGGER set_updated_at BEFORE UPDATE ON medications
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

CREATE TRIGGER set_updated_at BEFORE UPDATE ON medical_staff
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

CREATE TRIGGER set_updated_at BEFORE UPDATE ON alert_thresholds
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

-- ============================================================================
-- SEED DATA — Default alert thresholds (global template)
-- ============================================================================

-- These can be copied per-patient or used as system defaults.
-- Stored as a reference table.

CREATE TABLE default_thresholds (
    vital_type      VARCHAR(50)         NOT NULL,
    severity        alert_severity_enum NOT NULL,
    min_value       DECIMAL(10,2),
    max_value       DECIMAL(10,2),
    PRIMARY KEY (vital_type, severity)
);

INSERT INTO default_thresholds (vital_type, severity, min_value, max_value) VALUES
    -- Heart Rate (bpm)
    ('heart_rate',       'low',      55, 100),
    ('heart_rate',       'medium',   50, 110),
    ('heart_rate',       'high',     45, 130),
    ('heart_rate',       'critical', 40, 150),
    -- Systolic BP (mmHg)
    ('systolic_bp',      'low',      100, 130),
    ('systolic_bp',      'medium',   90,  140),
    ('systolic_bp',      'high',     80,  160),
    ('systolic_bp',      'critical', 70,  180),
    -- Diastolic BP (mmHg)
    ('diastolic_bp',     'low',      65, 85),
    ('diastolic_bp',     'medium',   60, 90),
    ('diastolic_bp',     'high',     55, 100),
    ('diastolic_bp',     'critical', 50, 110),
    -- SpO2 (%)
    ('spo2',             'low',      94, 100),
    ('spo2',             'medium',   90, 100),
    ('spo2',             'high',     85, 100),
    ('spo2',             'critical', 80, 100),
    -- Temperature (°C)
    ('temperature',      'low',      36.1, 37.5),
    ('temperature',      'medium',   35.5, 38.0),
    ('temperature',      'high',     35.0, 39.0),
    ('temperature',      'critical', 34.0, 40.5),
    -- Respiratory Rate (breaths/min)
    ('respiratory_rate', 'low',      12, 20),
    ('respiratory_rate', 'medium',   10, 24),
    ('respiratory_rate', 'high',     8,  28),
    ('respiratory_rate', 'critical', 6,  35);
