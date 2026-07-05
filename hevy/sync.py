"""Sync Hevy workouts (per-set strength data) into InfluxDB.

Polls the official Hevy API and writes two measurements:
- workout_set: one point per set (tags: exercise, muscle_group, set; fields:
  weight_kg, reps, rpe, volume_kg, set_type, workout_id)
- workout: one summary point per workout (fields: duration_min, exercise_count,
  set_count, volume_kg, title, workout_id)

Incremental via MAX(time) on the workout measurement. Designed to run as a
systemd oneshot on a timer -- see hevy/hevy-sync.service and hevy/hevy-sync.timer.
"""

import logging
import os
from datetime import UTC, datetime
from typing import Any

from influxdb_client_3 import InfluxDBClient3, Point
from scale.cloud_sync import fetch_last_timestamp

from hevy.client import HevyClient

log = logging.getLogger(__name__)

WORKOUT_MEASUREMENT = "workout"
SET_MEASUREMENT = "workout_set"


def _parse_time(value: str) -> datetime:
    ts = datetime.fromisoformat(str(value))
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def build_muscle_map(templates: list[dict[str, Any]]) -> dict[str, str]:
    return {t["id"]: t.get("primary_muscle_group", "unknown") for t in templates if "id" in t}


def workout_points(workout: dict[str, Any], muscle_map: dict[str, str]) -> list[Point]:
    """Map one Hevy workout to InfluxDB points (summary + one point per set)."""
    start = _parse_time(workout["start_time"])
    end = _parse_time(workout["end_time"]) if workout.get("end_time") else start
    workout_id = str(workout.get("id", ""))

    points: list[Point] = []
    set_count = 0
    total_volume = 0.0

    for ex_index, exercise in enumerate(workout.get("exercises", [])):
        title = str(exercise.get("title", "unknown"))
        muscle = muscle_map.get(str(exercise.get("exercise_template_id", "")), "unknown")
        for s in exercise.get("sets", []):
            weight = float(s["weight_kg"]) if s.get("weight_kg") is not None else None
            reps = int(s["reps"]) if s.get("reps") is not None else None
            volume = weight * reps if weight is not None and reps is not None else None
            set_count += 1
            total_volume += volume or 0.0

            point = (
                Point(SET_MEASUREMENT)
                .tag("exercise", title)
                .tag("muscle_group", muscle)
                .tag("set", str(s.get("index", 0)))
                # offset by exercise position so repeated exercise titles in one
                # workout stay unique series-wise
                .time(start.replace(microsecond=ex_index * 1000))
                .field("set_type", str(s.get("type", "normal")))
                .field("workout_id", workout_id)
            )
            if weight is not None:
                point = point.field("weight_kg", weight)
            if reps is not None:
                point = point.field("reps", reps)
            if volume is not None:
                point = point.field("volume_kg", volume)
            if s.get("rpe") is not None:
                point = point.field("rpe", float(s["rpe"]))
            points.append(point)

    summary = (
        Point(WORKOUT_MEASUREMENT)
        .tag("title", str(workout.get("title", "Workout")))
        .time(start)
        .field("workout_id", workout_id)
        .field("duration_min", round((end - start).total_seconds() / 60, 1))
        .field("exercise_count", len(workout.get("exercises", [])))
        .field("set_count", set_count)
        .field("volume_kg", round(total_volume, 1))
    )
    points.append(summary)
    return points


def fetch_new_workouts(client: HevyClient, last: datetime | None) -> list[dict[str, Any]]:
    """Fetch workouts newer than `last` (all workouts when last is None).

    Pages are newest-first; stop once an entire page is older than the mark.
    """
    new: list[dict[str, Any]] = []
    page = 1
    while True:
        workouts, page_count = client.workouts(page)
        new.extend(w for w in workouts if last is None or _parse_time(w["start_time"]) > last)
        page_is_stale = last is not None and workouts and all(_parse_time(w["start_time"]) <= last for w in workouts)
        if page >= page_count or page_is_stale:
            return new
        page += 1


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")

    hevy = HevyClient(api_key=os.environ["HEVY_API_KEY"])
    influx = InfluxDBClient3(
        host=os.environ.get("INFLUXDB_URL", "http://localhost:8181"),
        token=os.environ.get("INFLUXDB_TOKEN", ""),
        database=os.environ.get("INFLUXDB_DATABASE", "health"),
    )
    try:
        last = fetch_last_timestamp(influx, WORKOUT_MEASUREMENT)
        workouts = fetch_new_workouts(hevy, last)
        if workouts:
            muscle_map = build_muscle_map(hevy.exercise_templates())
            points = [p for w in workouts for p in workout_points(w, muscle_map)]
            influx.write(record=points)
        log.info("Hevy sync: wrote %s workouts (previous latest: %s)", len(workouts), last)
    finally:
        influx.close()


if __name__ == "__main__":
    main()
