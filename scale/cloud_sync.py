"""Sync S400 readings from the Xiaomi cloud into InfluxDB.

Runs SmartScaleConnect (github.com/AlexxIT/SmartScaleConnect) via docker to
poll the Mi Fitness / Xiaomi Home cloud into a JSON file, then ingests any
readings newer than what InfluxDB already has into the same body_composition
measurement the BLE reader writes. Designed to run as a systemd oneshot on a
timer -- see scale/scale-sync.service and scale/scale-sync.timer.
"""

import json
import logging
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from influxdb_client_3 import InfluxDBClient3

from scale.config import load_config
from scale.measurement.model.body_composition import BodyComposition
from scale.measurement.persistence.influx_mapper import to_influx_point

log = logging.getLogger(__name__)

DOCKER_IMAGE = "alexxit/smartscaleconnect:latest"
# Must match the `to: json <file>` destination in scale-sync/scaleconnect.yaml.
OUTPUT_FILE = "scale.json"
_SYNC_DIR = Path(__file__).resolve().parent.parent / "scale-sync"


def _ensure_utc(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def _opt_float(entry: dict[str, Any], key: str) -> float | None:
    value = entry.get(key)
    return float(value) if value is not None else None


def parse_entries(raw: str) -> list[dict[str, Any]]:
    """SmartScaleConnect json output is an array of core.Weight objects."""
    data = json.loads(raw)
    return data if isinstance(data, list) else []


def entry_timestamp(entry: dict[str, Any]) -> datetime:
    return _ensure_utc(datetime.fromisoformat(str(entry["Date"])))


def to_body_composition(entry: dict[str, Any]) -> BodyComposition:
    """Map SmartScaleConnect's core.Weight fields onto the BLE-path schema.

    Units per pkg/core/weight.go: BodyFat/BodyWater are percent,
    ProteinMass/BoneMass/MuscleMass are kg. Fields the scale didn't measure
    are omitted from the json -- they map to None and are not written.
    """
    weight = float(entry.get("Weight", 0.0))
    protein_mass = _opt_float(entry, "ProteinMass")
    bpm = int(entry.get("HeartRate", 0))
    metabolic_age = entry.get("MetabolicAge")
    body_type = entry.get("PhysiqueRating")
    return BodyComposition(
        weight_kg=weight,
        bmi=_opt_float(entry, "BMI"),
        body_fat_pct=_opt_float(entry, "BodyFat"),
        water_pct=_opt_float(entry, "BodyWater"),
        muscle_mass_kg=_opt_float(entry, "MuscleMass"),
        bone_mass_kg=_opt_float(entry, "BoneMass"),
        protein_pct=round(protein_mass / weight * 100, 1) if protein_mass and weight > 0 else None,
        visceral_fat=_opt_float(entry, "VisceralFat"),
        bmr_kcal=_opt_float(entry, "BasalMetabolism"),
        metabolic_age=int(metabolic_age) if metabolic_age is not None else None,
        ideal_weight_kg=None,  # not provided by the cloud
        body_type=int(body_type) if body_type is not None else None,
        heart_rate=bpm if bpm > 0 else None,
        impedance=None,  # the cloud returns derived metrics, not raw impedance
    )


def filter_new(entries: list[dict[str, Any]], last: datetime | None) -> list[dict[str, Any]]:
    return [e for e in entries if "Date" in e and (last is None or entry_timestamp(e) > last)]


def fetch_last_timestamp(client: InfluxDBClient3, measurement: str) -> datetime | None:
    try:
        table = client.query(query=f'SELECT MAX(time) AS last FROM "{measurement}"', language="sql")  # noqa: S608
    except Exception:  # noqa: BLE001 -- table does not exist until the first write
        return None
    rows = table.to_pylist()
    last = rows[0]["last"] if rows else None
    return _ensure_utc(last) if last else None


def run_scaleconnect(sync_dir: Path) -> None:
    """One-shot SmartScaleConnect run; reads scaleconnect.yaml in sync_dir."""
    # Remove any stale output so a config/OUTPUT_FILE mismatch fails loudly.
    (sync_dir / OUTPUT_FILE).unlink(missing_ok=True)
    subprocess.run(  # noqa: S603
        [
            "/usr/bin/docker",
            "run",
            "--rm",
            "-v",
            f"{sync_dir}:/data",
            "-w",
            "/data",
            DOCKER_IMAGE,
            # override the image's run.sh, which loops forever in repeat mode
            "scaleconnect",
        ],
        check=True,
        timeout=300,
    )


def main() -> None:
    cfg = load_config(Path(__file__).parent / "config.yaml")
    logging.basicConfig(level=cfg.log_level, format="%(asctime)s %(levelname)s %(message)s")

    run_scaleconnect(_SYNC_DIR)
    entries = parse_entries((_SYNC_DIR / OUTPUT_FILE).read_text())

    client = InfluxDBClient3(host=cfg.influx.url, token=cfg.influx.token, database=cfg.influx.database)
    try:
        last = fetch_last_timestamp(client, cfg.influx.measurement)
        new = filter_new(entries, last)
        points = [
            to_influx_point(
                to_body_composition(entry),
                entry_timestamp(entry),
                user=cfg.user.name,
                measurement_name=cfg.influx.measurement,
            )
            for entry in new
        ]
        if points:
            client.write(record=points)
        log.info("Cloud sync: wrote %s new of %s readings (previous latest: %s)", len(new), len(entries), last)
    finally:
        client.close()


if __name__ == "__main__":
    main()
