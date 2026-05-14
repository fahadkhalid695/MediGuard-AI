# MediGuard AI — Redis Caching Schema

## Overview

Redis serves as the **real-time caching layer** for MediGuard AI, providing sub-millisecond
access to the latest vitals, active alerts, and patient status. PostgreSQL remains the
source of truth; Redis is populated via application writes (write-through) and TTLs
ensure stale data is automatically evicted.

---

## Key Namespace Convention

All keys follow the pattern: `mediguard:{domain}:{entity_id}:{sub_key}`

---

## 1. Latest Vitals per Patient

**Purpose:** Instant dashboard access to the most recent vital signs without querying PostgreSQL.

```
Key:     mediguard:vitals:latest:{patient_id}
Type:    Hash
TTL:     300 seconds (5 minutes)

Fields:
  heart_rate        → "78"
  systolic_bp       → "122"
  diastolic_bp      → "80"
  spo2              → "98.5"
  temperature       → "36.8"
  respiratory_rate  → "16"
  blood_glucose     → "95.00"
  pain_level        → "2"
  source            → "monitor"
  device_id         → "MON-ICU-042"
  recorded_at       → "2026-05-14T23:45:00Z"
  is_anomalous      → "false"
```

**Write command (application-side):**
```redis
HSET mediguard:vitals:latest:{patient_id}
     heart_rate 78
     systolic_bp 122
     diastolic_bp 80
     spo2 98.5
     temperature 36.8
     respiratory_rate 16
     source monitor
     device_id MON-ICU-042
     recorded_at "2026-05-14T23:45:00Z"
     is_anomalous false

EXPIRE mediguard:vitals:latest:{patient_id} 300
```

**Read command:**
```redis
HGETALL mediguard:vitals:latest:{patient_id}
```

---

## 2. Vitals Time-Series Stream (Recent Window)

**Purpose:** Keep the last ~100 readings per patient in a Redis Stream for real-time
charting and WebSocket broadcasting without hitting PostgreSQL.

```
Key:     mediguard:vitals:stream:{patient_id}
Type:    Stream
MaxLen:  ~100 (approximate trimming)
TTL:     None (trimmed by MAXLEN)
```

**Write command:**
```redis
XADD mediguard:vitals:stream:{patient_id} MAXLEN ~ 100 *
     heart_rate 78
     systolic_bp 122
     diastolic_bp 80
     spo2 98.5
     temperature 36.8
     respiratory_rate 16
     recorded_at "2026-05-14T23:45:00Z"
```

**Read last 20 entries:**
```redis
XREVRANGE mediguard:vitals:stream:{patient_id} + - COUNT 20
```

**Consumer group for real-time processing:**
```redis
XGROUP CREATE mediguard:vitals:stream:{patient_id} alert_engine $ MKSTREAM
XREADGROUP GROUP alert_engine worker_1 COUNT 10 BLOCK 5000
    STREAMS mediguard:vitals:stream:{patient_id} >
```

---

## 3. Active Alerts per Patient

**Purpose:** Fast lookup of current unresolved alerts for a patient's dashboard card.

```
Key:     mediguard:alerts:active:{patient_id}
Type:    Sorted Set
Score:   Unix timestamp of alert creation
Member:  JSON-encoded alert object
TTL:     3600 seconds (1 hour, refreshed on write)
```

**Member format:**
```json
{
  "id": "a1b2c3d4-...",
  "severity": "critical",
  "title": "Heart Rate Critically High",
  "vital_type": "heart_rate",
  "vital_value": 165,
  "created_at": "2026-05-14T23:42:00Z"
}
```

**Write command:**
```redis
ZADD mediguard:alerts:active:{patient_id} 1747263720 '{"id":"a1b2c3d4-...","severity":"critical",...}'
EXPIRE mediguard:alerts:active:{patient_id} 3600
```

**Read recent alerts (newest first):**
```redis
ZREVRANGE mediguard:alerts:active:{patient_id} 0 9
```

**Remove resolved alert:**
```redis
ZREM mediguard:alerts:active:{patient_id} '{"id":"a1b2c3d4-...",...}'
```

---

## 4. Alert Counters (Global Dashboard)

**Purpose:** Real-time counters displayed on the admin/monitoring dashboard.

```
Key:     mediguard:alerts:count:{severity}
Type:    String (integer counter)
TTL:     None (managed by application)

Severity keys:
  mediguard:alerts:count:low
  mediguard:alerts:count:medium
  mediguard:alerts:count:high
  mediguard:alerts:count:critical
  mediguard:alerts:count:total
```

**Increment on new alert:**
```redis
INCR mediguard:alerts:count:critical
INCR mediguard:alerts:count:total
```

**Decrement on resolution:**
```redis
DECR mediguard:alerts:count:critical
DECR mediguard:alerts:count:total
```

---

## 5. Patient Status Cache

**Purpose:** Cache basic patient profile + assignment info to avoid JOINs on every dashboard load.

```
Key:     mediguard:patient:profile:{patient_id}
Type:    Hash
TTL:     600 seconds (10 minutes)

Fields:
  first_name          → "Ahmad"
  last_name           → "Khan"
  date_of_birth       → "1985-03-12"
  gender              → "male"
  blood_group         → "B+"
  is_active           → "true"
  primary_doctor_id   → "d4e5f6a7-..."
  primary_doctor_name → "Dr. Sarah Ahmed"
  conditions_count    → "3"
  medications_count   → "5"
```

---

## 6. Online / Connected Patients Set

**Purpose:** Track which patients currently have active monitoring devices connected
(for the real-time monitoring dashboard).

```
Key:     mediguard:patients:online
Type:    Set
TTL:     None (managed by heartbeat logic)
```

**Device connects:**
```redis
SADD mediguard:patients:online {patient_id}
```

**Device disconnects:**
```redis
SREM mediguard:patients:online {patient_id}
```

**Get all online patients:**
```redis
SMEMBERS mediguard:patients:online
```

**Count online patients:**
```redis
SCARD mediguard:patients:online
```

---

## 7. Pub/Sub Channels

**Purpose:** Real-time event broadcasting to WebSocket servers and alert processors.

```
Channels:
  mediguard:channel:vitals:{patient_id}     → New vital reading for a specific patient
  mediguard:channel:alerts:{patient_id}     → New alert for a specific patient
  mediguard:channel:alerts:broadcast        → All new alerts (for global dashboard)
  mediguard:channel:system                  → System-wide notifications
```

**Publish vital update:**
```redis
PUBLISH mediguard:channel:vitals:{patient_id} '{"heart_rate":78,"spo2":98.5,...}'
```

**Subscribe to patient alerts:**
```redis
SUBSCRIBE mediguard:channel:alerts:{patient_id}
```

**Pattern subscribe to all patient vitals:**
```redis
PSUBSCRIBE mediguard:channel:vitals:*
```

---

## 8. Rate Limiting / Deduplication

**Purpose:** Prevent alert storms — don't fire the same alert type for the same patient
more than once per cooldown window.

```
Key:     mediguard:alert:cooldown:{patient_id}:{vital_type}:{severity}
Type:    String (value: "1")
TTL:     60–300 seconds (configurable per severity)

Example: mediguard:alert:cooldown:abc123:heart_rate:critical
```

**Check + set (atomic):**
```redis
SET mediguard:alert:cooldown:{patient_id}:heart_rate:critical 1 NX EX 120
-- Returns OK if cooldown was set (fire alert)
-- Returns nil if cooldown exists (suppress alert)
```

---

## Data Flow Diagram

```
IoT Device / Monitor
        │
        ▼
  [Application Server]
        │
        ├──► PostgreSQL (INSERT INTO vitals)          — Source of truth
        │
        ├──► Redis HSET   (latest vitals)             — Dashboard cache
        ├──► Redis XADD   (vitals stream)             — Real-time charts
        ├──► Redis PUBLISH (vitals channel)            — WebSocket push
        │
        └──► [Alert Engine]
                │
                ├──► Redis SET NX (cooldown check)
                ├──► PostgreSQL (INSERT INTO alert_logs)
                ├──► Redis ZADD  (active alerts)
                ├──► Redis INCR  (alert counters)
                └──► Redis PUBLISH (alert channel)     — Push notifications
```
