"""
MediGuard AI — Mock Vitals Data Generator.

Simulates 10 patients with realistic vital signs, posting readings
to the FastAPI service at configurable intervals. Occasionally injects
anomalous readings to test alert detection.

Usage:
    python -m scripts.mock_data_generator
    python -m scripts.mock_data_generator --patients 20 --interval 2.0 --anomaly-rate 0.15
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

# ── Configuration ───────────────────────────────────────────────────────────

API_BASE_URL = "http://localhost:8000/api/v1/vitals"
DEFAULT_NUM_PATIENTS = 10
DEFAULT_INTERVAL_SECONDS = 3.0
DEFAULT_ANOMALY_RATE = 0.12  # 12% chance per reading

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-8s │ %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("mock_generator")


# ── Patient Profiles ────────────────────────────────────────────────────────

PATIENT_PROFILES = [
    {"name": "Ahmad Khan",       "age": 45, "baseline_hr": 72,  "baseline_spo2": 97.5},
    {"name": "Sarah Ahmed",      "age": 32, "baseline_hr": 68,  "baseline_spo2": 98.0},
    {"name": "Muhammad Ali",     "age": 67, "baseline_hr": 78,  "baseline_spo2": 95.5},
    {"name": "Fatima Zahra",     "age": 28, "baseline_hr": 65,  "baseline_spo2": 98.5},
    {"name": "Omar Farooq",     "age": 55, "baseline_hr": 75,  "baseline_spo2": 96.0},
    {"name": "Aisha Malik",      "age": 71, "baseline_hr": 82,  "baseline_spo2": 94.5},
    {"name": "Hassan Raza",      "age": 39, "baseline_hr": 70,  "baseline_spo2": 98.0},
    {"name": "Zainab Hussain",   "age": 50, "baseline_hr": 73,  "baseline_spo2": 97.0},
    {"name": "Bilal Sheikh",     "age": 62, "baseline_hr": 76,  "baseline_spo2": 96.5},
    {"name": "Mariam Qureshi",   "age": 44, "baseline_hr": 69,  "baseline_spo2": 98.0},
]


@dataclass
class SimulatedPatient:
    """A simulated patient with individual vitals baselines and drift."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    name: str = ""
    age: int = 40
    baseline_hr: int = 72
    baseline_spo2: float = 97.5

    # Running state for smooth drift simulation
    _current_hr: float = 0.0
    _current_systolic: float = 120.0
    _current_diastolic: float = 78.0
    _current_spo2: float = 97.5
    _current_temp: float = 36.7
    _current_rr: float = 16.0

    def __post_init__(self):
        self._current_hr = float(self.baseline_hr)
        self._current_spo2 = self.baseline_spo2
        # Older patients tend toward higher BP
        age_factor = max(0, (self.age - 30) * 0.5)
        self._current_systolic = 115.0 + age_factor
        self._current_diastolic = 72.0 + age_factor * 0.4

    def generate_normal_reading(self) -> dict:
        """Generate a normal vital reading with realistic drift."""

        # Smooth random walk (small step per reading)
        self._current_hr += random.gauss(0, 1.5)
        self._current_hr = max(50, min(110, self._current_hr))

        self._current_systolic += random.gauss(0, 2.0)
        self._current_systolic = max(90, min(150, self._current_systolic))

        self._current_diastolic += random.gauss(0, 1.2)
        self._current_diastolic = max(55, min(95, self._current_diastolic))

        # Ensure systolic > diastolic by at least 20
        if self._current_systolic - self._current_diastolic < 20:
            self._current_systolic = self._current_diastolic + 25

        self._current_spo2 += random.gauss(0, 0.3)
        self._current_spo2 = max(94, min(100, self._current_spo2))

        self._current_temp += random.gauss(0, 0.1)
        self._current_temp = max(36.0, min(37.5, self._current_temp))

        self._current_rr += random.gauss(0, 0.8)
        self._current_rr = max(12, min(22, self._current_rr))

        return {
            "patient_id": str(self.id),
            "heart_rate": round(self._current_hr),
            "bp_systolic": round(self._current_systolic),
            "bp_diastolic": round(self._current_diastolic),
            "spo2": round(self._current_spo2, 1),
            "temperature": round(self._current_temp, 1),
            "respiratory_rate": round(self._current_rr),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def generate_anomalous_reading(self) -> dict:
        """
        Generate an anomalous vital reading.

        Randomly picks one or more anomaly types to simulate
        realistic clinical emergencies.
        """
        reading = self.generate_normal_reading()

        # Pick 1–2 anomaly types
        anomaly_types = random.sample(
            ["bradycardia", "tachycardia", "hypotension", "hypertension",
             "desaturation", "fever", "hypothermia", "tachypnea"],
            k=random.randint(1, 2),
        )

        for anomaly in anomaly_types:
            match anomaly:
                case "bradycardia":
                    reading["heart_rate"] = random.randint(30, 48)
                    logger.warning("  💓 Injecting BRADYCARDIA: HR=%d", reading["heart_rate"])

                case "tachycardia":
                    reading["heart_rate"] = random.randint(130, 180)
                    logger.warning("  💓 Injecting TACHYCARDIA: HR=%d", reading["heart_rate"])

                case "hypotension":
                    reading["bp_systolic"] = random.randint(60, 82)
                    reading["bp_diastolic"] = random.randint(30, 50)
                    logger.warning(
                        "  🩸 Injecting HYPOTENSION: BP=%d/%d",
                        reading["bp_systolic"], reading["bp_diastolic"],
                    )

                case "hypertension":
                    reading["bp_systolic"] = random.randint(170, 220)
                    reading["bp_diastolic"] = random.randint(105, 130)
                    logger.warning(
                        "  🩸 Injecting HYPERTENSION: BP=%d/%d",
                        reading["bp_systolic"], reading["bp_diastolic"],
                    )

                case "desaturation":
                    reading["spo2"] = round(random.uniform(78.0, 89.0), 1)
                    logger.warning("  🫁 Injecting DESATURATION: SpO2=%.1f%%", reading["spo2"])

                case "fever":
                    reading["temperature"] = round(random.uniform(38.8, 41.0), 1)
                    logger.warning("  🌡 Injecting FEVER: Temp=%.1f°C", reading["temperature"])

                case "hypothermia":
                    reading["temperature"] = round(random.uniform(33.0, 35.0), 1)
                    logger.warning("  🌡 Injecting HYPOTHERMIA: Temp=%.1f°C", reading["temperature"])

                case "tachypnea":
                    reading["respiratory_rate"] = random.randint(28, 45)
                    logger.warning("  🌬 Injecting TACHYPNEA: RR=%d", reading["respiratory_rate"])

        # Fix BP consistency after anomaly injection
        if reading["bp_systolic"] <= reading["bp_diastolic"]:
            reading["bp_systolic"] = reading["bp_diastolic"] + 25

        return reading


# ── Simulation Engine ───────────────────────────────────────────────────────

class VitalsSimulator:
    """Manages multiple simulated patients and sends vitals to the API."""

    def __init__(
        self,
        num_patients: int = DEFAULT_NUM_PATIENTS,
        interval: float = DEFAULT_INTERVAL_SECONDS,
        anomaly_rate: float = DEFAULT_ANOMALY_RATE,
        api_url: str = API_BASE_URL,
    ):
        self.interval = interval
        self.anomaly_rate = anomaly_rate
        self.api_url = api_url
        self.patients: list[SimulatedPatient] = []
        self.stats = {"sent": 0, "anomalies": 0, "errors": 0}

        # Create patients from profiles (or generate extras if needed)
        for i in range(num_patients):
            profile = PATIENT_PROFILES[i % len(PATIENT_PROFILES)]
            patient = SimulatedPatient(
                name=profile["name"],
                age=profile["age"],
                baseline_hr=profile["baseline_hr"],
                baseline_spo2=profile["baseline_spo2"],
            )
            self.patients.append(patient)

    async def run(self, duration: int | None = None) -> None:
        """
        Run the simulation loop.

        Args:
            duration: Total duration in seconds. None = run forever.
        """
        logger.info("=" * 65)
        logger.info("🏥  MediGuard AI — Mock Vitals Generator")
        logger.info("=" * 65)
        logger.info("  Patients:      %d", len(self.patients))
        logger.info("  Interval:      %.1fs", self.interval)
        logger.info("  Anomaly rate:  %.0f%%", self.anomaly_rate * 100)
        logger.info("  API endpoint:  %s", self.api_url)
        logger.info("=" * 65)

        for i, p in enumerate(self.patients):
            logger.info("  Patient %02d: %-20s (ID: %s)", i + 1, p.name, p.id)

        logger.info("-" * 65)
        logger.info("Starting simulation... Press Ctrl+C to stop.\n")

        elapsed = 0.0
        round_num = 0

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                while True:
                    round_num += 1
                    logger.info("━━━ Round %d ━━━", round_num)

                    # Send readings for all patients concurrently
                    tasks = [
                        self._send_reading(client, patient)
                        for patient in self.patients
                    ]
                    await asyncio.gather(*tasks, return_exceptions=True)

                    # Stats summary
                    logger.info(
                        "📊 Totals: sent=%d | anomalies=%d | errors=%d\n",
                        self.stats["sent"],
                        self.stats["anomalies"],
                        self.stats["errors"],
                    )

                    await asyncio.sleep(self.interval)
                    elapsed += self.interval

                    if duration and elapsed >= duration:
                        logger.info("⏰ Duration limit reached (%ds). Stopping.", duration)
                        break

            except KeyboardInterrupt:
                logger.info("\n🛑 Simulation stopped by user.")

        # Final stats
        logger.info("=" * 65)
        logger.info("📈 Final Statistics")
        logger.info("  Total readings sent:  %d", self.stats["sent"])
        logger.info("  Anomalous readings:   %d", self.stats["anomalies"])
        logger.info("  Failed requests:      %d", self.stats["errors"])
        logger.info("=" * 65)

    async def _send_reading(
        self,
        client: httpx.AsyncClient,
        patient: SimulatedPatient,
    ) -> None:
        """Generate and send a single vital reading for one patient."""

        is_anomalous = random.random() < self.anomaly_rate

        if is_anomalous:
            reading = patient.generate_anomalous_reading()
            self.stats["anomalies"] += 1
        else:
            reading = patient.generate_normal_reading()

        try:
            response = await client.post(self.api_url, json=reading)

            if response.status_code == 201:
                result = response.json()
                status_icon = "🔴" if result.get("is_anomalous") else "🟢"
                logger.info(
                    "  %s %-18s │ HR:%3d │ BP:%3d/%3d │ SpO2:%5.1f%% │ T:%4.1f°C │ cached=%s",
                    status_icon,
                    patient.name,
                    reading["heart_rate"],
                    reading["bp_systolic"],
                    reading["bp_diastolic"],
                    reading["spo2"],
                    reading["temperature"],
                    result.get("cached", "?"),
                )
                self.stats["sent"] += 1
            else:
                logger.error(
                    "  ❌ %-18s │ HTTP %d: %s",
                    patient.name,
                    response.status_code,
                    response.text[:200],
                )
                self.stats["errors"] += 1

        except httpx.RequestError as exc:
            logger.error("  ❌ %-18s │ Connection error: %s", patient.name, exc)
            self.stats["errors"] += 1


# ── CLI Entry Point ─────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="MediGuard AI — Mock Vitals Data Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m scripts.mock_data_generator
  python -m scripts.mock_data_generator --patients 5 --interval 1.0
  python -m scripts.mock_data_generator --anomaly-rate 0.25 --duration 60
        """,
    )
    parser.add_argument(
        "--patients", "-p",
        type=int,
        default=DEFAULT_NUM_PATIENTS,
        help=f"Number of simulated patients (default: {DEFAULT_NUM_PATIENTS})",
    )
    parser.add_argument(
        "--interval", "-i",
        type=float,
        default=DEFAULT_INTERVAL_SECONDS,
        help=f"Seconds between reading rounds (default: {DEFAULT_INTERVAL_SECONDS})",
    )
    parser.add_argument(
        "--anomaly-rate", "-a",
        type=float,
        default=DEFAULT_ANOMALY_RATE,
        help=f"Probability of anomalous reading per patient (default: {DEFAULT_ANOMALY_RATE})",
    )
    parser.add_argument(
        "--duration", "-d",
        type=int,
        default=None,
        help="Total simulation duration in seconds (default: run forever)",
    )
    parser.add_argument(
        "--url",
        type=str,
        default=API_BASE_URL,
        help=f"API endpoint URL (default: {API_BASE_URL})",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()

    simulator = VitalsSimulator(
        num_patients=args.patients,
        interval=args.interval,
        anomaly_rate=args.anomaly_rate,
        api_url=args.url,
    )

    await simulator.run(duration=args.duration)


if __name__ == "__main__":
    asyncio.run(main())
