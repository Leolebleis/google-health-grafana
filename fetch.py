#!/usr/bin/env python3
"""Google Health API -> InfluxDB fetcher.

Pulls health data from the Google Health API (v4) and writes it to InfluxDB.
Outputs in fitbit-grafana compatible schema so pre-built dashboards work.

Requires OAuth credentials with health-only scopes (no cloud-platform).
"""

import json
import logging
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from influxdb_client_3 import InfluxDBClient3, Point

HEALTH_API = "https://health.googleapis.com/v4"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


def load_config() -> dict:
    return {
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
        "token_path": Path(os.environ.get("TOKEN_PATH", "/app/tokens/token.json")),
        "influx_host": os.environ.get("INFLUXDB_HOST", "localhost"),
        "influx_port": int(os.environ.get("INFLUXDB_PORT", "8181")),
        "influx_token": os.environ.get("INFLUXDB_TOKEN", ""),
        "influx_database": os.environ.get("INFLUXDB_DATABASE", "health"),
        "device_name": os.environ.get("DEVICE_NAME", "Pixel Watch"),
        "backfill_days": int(os.environ.get("BACKFILL_DAYS", "7")),
        "interval_seconds": int(os.environ.get("INTERVAL_SECONDS", "300")),
    }


# --- OAuth ---


def load_tokens(path: Path) -> dict:
    with path.open() as f:
        return json.load(f)


def save_tokens(path: Path, tokens: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(tokens, f)


def refresh_access_token(cfg: dict) -> str:
    tokens = load_tokens(cfg["token_path"])
    data = urlencode(
        {
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "refresh_token": tokens["refresh_token"],
            "grant_type": "refresh_token",
        }
    ).encode()
    resp = json.loads(urlopen(Request(TOKEN_URL, data=data)).read())  # noqa: S310
    tokens["access_token"] = resp["access_token"]
    save_tokens(cfg["token_path"], tokens)
    log.info("Token refreshed")
    return resp["access_token"]


# --- API helpers ---


def api_get(token: str, path: str) -> dict:
    req = Request(f"{HEALTH_API}{path}", headers={"Authorization": f"Bearer {token}"})  # noqa: S310
    return json.loads(urlopen(req).read())  # noqa: S310


def api_post(token: str, path: str, body: dict) -> dict:
    req = Request(  # noqa: S310
        f"{HEALTH_API}{path}",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    return json.loads(urlopen(req).read())  # noqa: S310


def date_to_civil(d: datetime) -> dict:
    return {"date": {"year": d.year, "month": d.month, "day": d.day}}


def civil_to_datetime(civil: dict) -> datetime:
    d = civil.get("date", {})
    t = civil.get("time", {})
    return datetime(
        d.get("year", 2000),
        d.get("month", 1),
        d.get("day", 1),
        t.get("hours", 0),
        t.get("minutes", 0),
        t.get("seconds", 0),
        tzinfo=UTC,
    )


def parse_time(val: dict) -> str:
    """Extract timestamp from API response -- handles both sampleTime and date formats."""
    st = val.get("sampleTime", {})
    if "physicalTime" in st:
        return st["physicalTime"]
    date_dict = val.get("date")
    if date_dict:
        return civil_to_datetime({"date": date_dict}).isoformat()
    return ""


def pt(measurement: str, ts: str, fields: dict) -> dict:
    return {"measurement": measurement, "time": ts, "fields": fields}


# --- Fetchers (output in fitbit-grafana compatible schema) ---


def fetch_weight(token: str) -> list:
    """Measurement: weight, fields: value (kg), bmi"""
    points = []
    data = api_get(token, "/users/me/dataTypes/weight/dataPoints?pageSize=1000")
    for dp in data.get("dataPoints", []):
        w = dp.get("weight", {})
        ts = parse_time(w)
        if not ts:
            continue
        kg = w.get("weightGrams", 0) / 1000
        points.append(pt("weight", ts, {"value": kg}))
    log.info("Fetched weight: %s points", len(points))
    return points


def fetch_body_fat(token: str) -> list:
    """Measurement: body_fat, fields: value (percentage)"""
    points = []
    data = api_get(token, "/users/me/dataTypes/body-fat/dataPoints?pageSize=1000")
    for dp in data.get("dataPoints", []):
        bf = dp.get("bodyFat", {})
        ts = parse_time(bf)
        if not ts:
            continue
        points.append(pt("body_fat", ts, {"value": bf.get("bodyFatPercentage", 0)}))
    log.info("Fetched body fat: %s points", len(points))
    return points


def fetch_heart_rate_intraday(token: str) -> list:
    """Measurement: HeartRate_Intraday, fields: value (bpm)"""
    points = []
    try:
        data = api_get(token, "/users/me/dataTypes/heart-rate/dataPoints?pageSize=10000")
        for dp in data.get("dataPoints", []):
            hr = dp.get("heartRate", {})
            ts = parse_time(hr)
            bpm = hr.get("beatsPerMinute")
            if ts and bpm:
                points.append(pt("HeartRate_Intraday", ts, {"value": int(bpm)}))
        log.info("Fetched heart rate intraday: %s points", len(points))
    except HTTPError as e:
        log.warning("Failed to fetch heart rate: %s", e.code)
    return points


def fetch_daily_metrics(token: str) -> list:
    """Fetch resting HR, HRV, SpO2, respiratory rate in fitbit-grafana schema."""
    points = []
    metrics = [
        (
            "daily-resting-heart-rate",
            "dailyRestingHeartRate",
            "RestingHR",
            lambda v: {"value": int(v.get("beatsPerMinute", 0))},
        ),
        (
            "daily-heart-rate-variability",
            "dailyHeartRateVariability",
            "HRV",
            lambda v: {"dailyRmssd": float(v.get("averageHeartRateVariabilityMilliseconds", 0))},
        ),
        (
            "daily-oxygen-saturation",
            "dailyOxygenSaturation",
            "SPO2",
            lambda v: {"avg": float(v.get("averagePercentage", 0))},
        ),
        (
            "daily-respiratory-rate",
            "dailyRespiratoryRate",
            "BreathingRate",
            lambda v: {"value": float(v.get("breathsPerMinute", 0))},
        ),
    ]
    for api_type, json_key, measurement, extract in metrics:
        try:
            data = api_get(token, f"/users/me/dataTypes/{api_type}/dataPoints?pageSize=100")
            count = 0
            for dp in data.get("dataPoints", []):
                val = dp.get(json_key, {})
                ts = parse_time(val)
                if not ts:
                    continue
                fields = extract(val)
                if any(v for v in fields.values()):
                    points.append(pt(measurement, ts, fields))
                    count += 1
            log.info("Fetched %s: %s points", api_type, count)
        except HTTPError as e:
            log.warning("Failed to fetch %s: %s", api_type, e.code)
    return points


def fetch_sleep(token: str) -> list:
    """Measurements: Sleep Summary + Sleep Levels (per-stage rows)."""
    points = []
    try:
        page_token = None
        sessions = 0
        while True:
            url = "/users/me/dataTypes/sleep/dataPoints?pageSize=10"
            if page_token:
                url += f"&pageToken={page_token}"
            data = api_get(token, url)
            for dp in data.get("dataPoints", []):
                sleep = dp.get("sleep", {})
                interval = sleep.get("interval", {})
                start_time = interval.get("startTime", "")
                if not start_time:
                    continue
                sessions += 1
                summary = sleep.get("summary", {})
                stage_summary = {s["type"].lower(): int(s.get("minutes", 0)) for s in summary.get("stagesSummary", [])}
                points.append(
                    pt(
                        "Sleep Summary",
                        start_time,
                        {
                            "minutesAsleep": float(summary.get("minutesAsleep", 0)),
                            "minutesAwake": float(summary.get("minutesAwake", 0)),
                            "minutesDeep": float(stage_summary.get("deep", 0)),
                            "minutesLight": float(stage_summary.get("light", 0)),
                            "minutesREM": float(stage_summary.get("rem", 0)),
                        },
                    )
                )
                level_map = {"AWAKE": 3.0, "REM": 2.0, "LIGHT": 1.0, "DEEP": 0.0}
                for stage in sleep.get("stages", []):
                    stage_start = stage.get("startTime", "")
                    if stage_start:
                        points.append(
                            pt(
                                "Sleep Levels",
                                stage_start,
                                {
                                    "level": level_map.get(stage.get("type", ""), 1.0),
                                },
                            )
                        )
            page_token = data.get("nextPageToken")
            if not page_token or sessions >= 30:  # noqa: PLR2004
                break
        log.info("Fetched sleep: %s sessions", sessions)
    except HTTPError as e:
        log.warning("Failed to fetch sleep: %s", e.code)
    return points


def fetch_spo2_intraday(token: str) -> list:
    """Measurement: SPO2_Intraday, fields: value"""
    points = []
    try:
        data = api_get(token, "/users/me/dataTypes/oxygen-saturation/dataPoints?pageSize=1000")
        for dp in data.get("dataPoints", []):
            o2 = dp.get("oxygenSaturation", {})
            ts = parse_time(o2)
            pct = o2.get("percentage")
            if ts and pct:
                points.append(pt("SPO2_Intraday", ts, {"value": float(pct)}))
        log.info("Fetched SpO2 intraday: %s points", len(points))
    except HTTPError as e:
        log.warning("Failed to fetch SpO2 intraday: %s", e.code)
    return points


def fetch_daily_rollups(token: str, start: datetime, end: datetime) -> list:
    """Fetch daily aggregated data -- steps, distance, calories, activity minutes."""
    points = []
    full_body = {"range": {"start": date_to_civil(start), "end": date_to_civil(end)}}
    short_start = max(start, end - timedelta(days=14))
    short_body = {"range": {"start": date_to_civil(short_start), "end": date_to_civil(end)}}

    rollups = [
        (
            "steps",
            False,
            "Total Steps",
            lambda v: {"value": float(v.get("steps", {}).get("countSum", 0))},
        ),
        (
            "distance",
            False,
            "distance",
            lambda v: {"value": float(v.get("distance", {}).get("millimetersSum", 0)) / 1000},
        ),
        (
            "total-calories",
            True,
            "calories",
            lambda v: {"value": round(float(v.get("totalCalories", {}).get("kcalSum", 0)), 1)},
        ),
        (
            "active-minutes",
            True,
            "Activity Minutes",
            _parse_active_minutes,
        ),
    ]

    for api_type, short_range, measurement, extract in rollups:
        body = short_body if short_range else full_body
        try:
            data = api_post(token, f"/users/me/dataTypes/{api_type}/dataPoints:dailyRollUp", body)
            count = 0
            for dp in data.get("rollupDataPoints", []):
                ts = civil_to_datetime(dp.get("civilStartTime", {}))
                fields = extract(dp)
                if any(v for v in fields.values()):
                    points.append(pt(measurement, ts.isoformat(), fields))
                    count += 1
            log.info("Fetched %s rollup: %s days", api_type, count)
        except HTTPError as e:
            log.warning("Failed to fetch %s rollup: %s", api_type, e.code)
    return points


def _parse_active_minutes(dp: dict) -> dict:
    level_map = {
        "LIGHT": "minutesLightlyActive",
        "MODERATE": "minutesFairlyActive",
        "VIGOROUS": "minutesVeryActive",
        "SEDENTARY": "minutesSedentary",
    }
    levels = dp.get("activeMinutes", {}).get("activeMinutesRollupByActivityLevel", [])
    result = {}
    for level in levels:
        key = level_map.get(level.get("activityLevel", ""))
        if key:
            result[key] = float(level.get("activeMinutesSum", 0))
    return result


def fetch_exercises(token: str) -> list:
    """Measurement: Activity Records"""
    points = []
    try:
        data = api_get(token, "/users/me/dataTypes/exercise/dataPoints?pageSize=25")
        for dp in data.get("dataPoints", []):
            ex = dp.get("exercise", {})
            interval = ex.get("interval", {})
            start_time = interval.get("startTime", "")
            if not start_time:
                continue
            points.append(
                pt(
                    "Activity Records",
                    start_time,
                    {
                        "duration": float(ex.get("durationMinutes", 0)),
                        "calories": float(ex.get("calories", 0)),
                        "activityName": ex.get("exerciseType", "unknown"),
                    },
                )
            )
        log.info("Fetched exercises: %s activities", len(points))
    except HTTPError as e:
        log.warning("Failed to fetch exercises: %s", e.code)
    return points


# --- InfluxDB ---


def write_to_influx(cfg: dict, points: list) -> None:
    if not points:
        return
    client = InfluxDBClient3(
        host=cfg["influx_host"],
        port=cfg["influx_port"],
        token=cfg["influx_token"],
        database=cfg["influx_database"],
    )

    influx_points = []
    for p in points:
        fields = {}
        for k, v in p["fields"].items():
            if v is None:
                continue
            fields[k] = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v
        if not fields:
            continue
        point = Point(p["measurement"]).tag("Device", cfg["device_name"]).time(p["time"])
        for k, v in fields.items():
            point = point.field(k, v)
        influx_points.append(point)

    if influx_points:
        client.write(record=influx_points)
        log.info("Wrote %s points to InfluxDB", len(influx_points))

    client.close()


# --- Main loop ---


def run_once(cfg: dict) -> None:
    token = refresh_access_token(cfg)
    now = datetime.now(UTC)
    start = now - timedelta(days=cfg["backfill_days"])

    all_points = []
    all_points.extend(fetch_weight(token))
    all_points.extend(fetch_body_fat(token))
    all_points.extend(fetch_heart_rate_intraday(token))
    all_points.extend(fetch_daily_metrics(token))
    all_points.extend(fetch_sleep(token))
    all_points.extend(fetch_spo2_intraday(token))
    all_points.extend(fetch_daily_rollups(token, start, now))
    all_points.extend(fetch_exercises(token))

    write_to_influx(cfg, all_points)
    log.info("Sync complete: %s total points", len(all_points))


def main() -> None:
    cfg = load_config()
    log.info("Starting google-health-grafana fetcher")

    while True:
        try:
            run_once(cfg)
        except Exception:
            log.exception("Sync failed")
        log.info("Sleeping %ss", cfg["interval_seconds"])
        time.sleep(cfg["interval_seconds"])


if __name__ == "__main__":
    main()
