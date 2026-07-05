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

from nutrition.target import LastGood, TargetConfig, compute_target

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
        "influx_url": os.environ.get("INFLUXDB_URL", "http://localhost:8181"),
        "influx_token": os.environ.get("INFLUXDB_TOKEN", ""),
        "influx_database": os.environ.get("INFLUXDB_DATABASE", "health"),
        "device_name": os.environ.get("DEVICE_NAME", "Pixel Watch"),
        "backfill_days": int(os.environ.get("BACKFILL_DAYS", "7")),
        # After the first full-backfill sync, only fetch/write points from the
        # last N days. Rewriting the full history every cycle explodes InfluxDB
        # 3 Core's parquet file count (no compaction) until queries hit the
        # file limit (see --query-file-limit in docker-compose.yml).
        "incremental_window_days": int(os.environ.get("INCREMENTAL_WINDOW_DAYS", "2")),
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
            # same measurement/timestamps as total-calories, so the fields merge.
            # unlike total-calories, this rollup accepts the full window.
            "active-energy-burned",
            False,
            "calories",
            lambda v: {"active": round(float(v.get("activeEnergyBurned", {}).get("kcalSum", 0)), 1)},
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


def _protein_grams(nutrition_log: dict) -> float:
    for n in nutrition_log.get("nutrients", []):
        if n.get("nutrient") == "PROTEIN":
            return float(n.get("quantity", {}).get("grams", 0))
    return 0.0


def _nutrition_daily_totals(raw_points: list) -> dict:
    """Collapse nutrition-log entries into daily totals.

    MFP sends each meal as a summary row (blank or "... Summary" food name,
    tagged with its mealType) plus the individual food-item rows. The summary
    rows are the authoritative meal totals; the item rows are their expansion
    and are NOT reliably summable -- items can be duplicated and can carry a
    different mealType than their summary (e.g. a SNACK summary whose items are
    tagged ANYTIME). Matching summaries to items by mealType therefore
    double-counts, so we never do it: if a day has any summary rows, the daily
    total is the sum of one summary per mealType; only a day with no summary
    rows at all falls back to summing its item rows.
    """
    summaries: dict = {}  # (day, mealType) -> entry, one summary per meal
    items: dict = {}  # day -> list[entry]
    for dp in raw_points:
        n = dp.get("nutritionLog", {})
        date_dict = n.get("interval", {}).get("civilStartTime", {}).get("date")
        if not date_dict:
            continue
        day = civil_to_datetime({"date": date_dict})
        name = n.get("foodDisplayName") or ""
        entry = {
            "caloriesIn": float(n.get("energy", {}).get("kcal", 0)),
            "protein": _protein_grams(n),
            "carbs": float(n.get("totalCarbohydrate", {}).get("grams", 0)),
            "fat": float(n.get("totalFat", {}).get("grams", 0)),
        }
        if not name or name.endswith("Summary"):
            summaries.setdefault((day, n.get("mealType", "UNKNOWN")), entry)
        else:
            items.setdefault(day, []).append(entry)

    days: dict = {}

    def add(day: datetime, entry: dict) -> None:
        totals = days.setdefault(day, {"caloriesIn": 0.0, "protein": 0.0, "carbs": 0.0, "fat": 0.0})
        for k in totals:
            totals[k] = round(totals[k] + entry[k], 1)

    summarized_days = {day for (day, _meal) in summaries}
    for (day, _meal), entry in summaries.items():
        add(day, entry)
    for day, entries in items.items():
        if day not in summarized_days:
            for entry in entries:
                add(day, entry)
    return days


def fetch_nutrition(token: str, start: datetime) -> list:
    """Measurement: Nutrition, fields: caloriesIn, protein, carbs, fat (daily totals from MFP)."""
    raw = []
    try:
        page_token = None
        while True:
            url = "/users/me/dataTypes/nutrition-log/dataPoints?pageSize=100"
            if page_token:
                url += f"&pageToken={page_token}"
            data = api_get(token, url)
            page = data.get("dataPoints", [])
            raw.extend(page)
            # newest-first; stop once a whole page predates the fetch window
            page_days = [
                civil_to_datetime({"date": d})
                for dp in page
                if (d := dp.get("nutritionLog", {}).get("interval", {}).get("civilStartTime", {}).get("date"))
            ]
            page_token = data.get("nextPageToken")
            if not page_token or (page_days and max(page_days) < start):
                break
    except HTTPError as e:
        log.warning("Failed to fetch nutrition: %s", e.code)

    points = [pt("Nutrition", day.isoformat(), totals) for day, totals in _nutrition_daily_totals(raw).items()]
    log.info("Fetched nutrition: %s days", len(points))
    return points


# --- InfluxDB ---


def _influx_client(cfg: dict) -> InfluxDBClient3:
    return InfluxDBClient3(
        host=cfg["influx_url"],
        token=cfg["influx_token"],
        database=cfg["influx_database"],
    )


def write_to_influx(cfg: dict, points: list) -> None:
    if not points:
        return
    client = _influx_client(cfg)

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


# --- Adaptive calorie target (design: docs/superpowers/specs/2026-07-05-adaptive-calorie-target-design.md) ---


def _influx_rows(client, sql: str, expected_missing: bool = False) -> list:
    """Run a query, returning [] on failure.

    expected_missing: the query touches a table/columns that legitimately may
    not exist yet (cold start) -- log at debug instead of warning.
    """
    try:
        return client.query(sql).to_pylist()
    except Exception as e:
        (log.debug if expected_missing else log.warning)("calorie-target query failed: %s", e)
        return []


def _row_day(row: dict):
    ts = row["time"]
    return ts.date() if hasattr(ts, "date") else _parse_point_time(ts).date()


def update_calorie_target(cfg: dict) -> None:
    """Back-calculate maintenance from logged intake vs weight trend; write calorie_target.

    Reads from InfluxDB rather than the in-memory fetch data: incremental API
    fetches only cover 2 days, and the scale's body_composition points never
    pass through this process at all. The DB is the one complete store.
    """
    client = _influx_client(cfg)
    # window_days + 1-day boundary buffer, derived so the SQL follows the constant
    window = f"now() - interval '{TargetConfig().window_days + 1} days'"
    try:
        intake_rows = _influx_rows(client, f'SELECT time, "caloriesIn" FROM "Nutrition" WHERE time >= {window}')
        weight_rows = _influx_rows(client, f'SELECT time, "weight" FROM "body_composition" WHERE time >= {window}')
        last_rows = _influx_rows(
            client,
            "SELECT \"target\", \"maintenance\" FROM \"calorie_target\" "
            "WHERE \"status\" = 'ok' AND time >= now() - interval '90 days' ORDER BY time DESC LIMIT 1",
            expected_missing=True,  # no ok point exists until the first live target
        )
    finally:
        client.close()

    intake = [(_row_day(r), float(r["caloriesIn"])) for r in intake_rows if r.get("caloriesIn") is not None]
    weights = [(_row_day(r), float(r["weight"])) for r in weight_rows if r.get("weight") is not None]
    last_good = None
    if last_rows and last_rows[0].get("target") is not None and last_rows[0].get("maintenance") is not None:
        last_good = LastGood(target=float(last_rows[0]["target"]), maintenance=float(last_rows[0]["maintenance"]))

    today = datetime.now(UTC).date()
    res = compute_target(intake, weights, last_good, today)

    midnight = datetime(today.year, today.month, today.day, tzinfo=UTC)
    fields = {
        "status": res.status,
        "logged_days": res.logged_days,
        "weighins": res.weighins,
        "target": res.target,
        "maintenance": res.maintenance,
        "intake_mean": res.intake_mean,
        "weight_rate_kg_wk": res.weight_rate_kg_wk,
    }
    write_to_influx(cfg, [pt("calorie_target", midnight.isoformat(), fields)])
    log.info(
        "Calorie target: %s kcal (%s, %s logged days, %s weigh-ins)",
        res.target,
        res.status,
        res.logged_days,
        res.weighins,
    )


# --- Main loop ---


def _parse_point_time(val: object) -> datetime | None:
    try:
        ts = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def _filter_window(points: list, cutoff: datetime) -> list:
    """Drop points older than cutoff; keep points with unparseable timestamps."""
    kept = []
    for p in points:
        ts = _parse_point_time(p["time"])
        if ts is None or ts >= cutoff:
            kept.append(p)
    return kept


def run_once(cfg: dict, window_days: int) -> None:
    token = refresh_access_token(cfg)
    now = datetime.now(UTC)
    start = now - timedelta(days=window_days)

    all_points = []
    all_points.extend(fetch_weight(token))
    all_points.extend(fetch_body_fat(token))
    all_points.extend(fetch_heart_rate_intraday(token))
    all_points.extend(fetch_daily_metrics(token))
    all_points.extend(fetch_sleep(token))
    all_points.extend(fetch_spo2_intraday(token))
    all_points.extend(fetch_daily_rollups(token, start, now))
    all_points.extend(fetch_exercises(token))
    all_points.extend(fetch_nutrition(token, start))

    points = _filter_window(all_points, now - timedelta(days=window_days))
    write_to_influx(cfg, points)
    log.info("Sync complete: wrote %s points (%s fetched, window=%sd)", len(points), len(all_points), window_days)
    try:
        update_calorie_target(cfg)
    except Exception:
        log.exception("Calorie target update failed (sync unaffected)")


def main() -> None:
    cfg = load_config()
    log.info("Starting google-health-grafana fetcher")

    window_days = cfg["backfill_days"]
    while True:
        try:
            run_once(cfg, window_days)
            window_days = min(cfg["backfill_days"], cfg["incremental_window_days"])
        except Exception:
            log.exception("Sync failed")
        log.info("Sleeping %ss", cfg["interval_seconds"])
        time.sleep(cfg["interval_seconds"])


if __name__ == "__main__":
    main()
