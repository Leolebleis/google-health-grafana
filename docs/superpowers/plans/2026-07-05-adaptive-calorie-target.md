# Adaptive Calorie Target Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compute a fault-tolerant adaptive daily calorie target (maintenance − 550) from 21 days of logged intake vs bodyweight trend, write it to a `calorie_target` InfluxDB measurement every fetch cycle, and surface it on the Grafana Health dashboard — replacing all uses of the unreliable watch calories-out.

**Architecture:** Pure, fully-tested math in a new `nutrition/` package (`compute_target`); dumb untested glue in `fetch.py` (3 InfluxDB reads → compute → 1 write, isolated by try/except so it can never break the main sync); dashboard changes in `dashboard.json` deployed via Grafana's HTTP API.

**Tech Stack:** Python 3.13 stdlib only (no new deps), pytest, ruff (`select = ALL`), ty, InfluxDB 3 Core (SQL via `InfluxDBClient3.query`, InfluxQL in Grafana panels), Grafana.

**Spec:** `docs/superpowers/specs/2026-07-05-adaptive-calorie-target-design.md` — read it first.

## Global Constraints

- Work on the existing branch `feat/adaptive-calorie-target` (already holds the spec commit). `main` is protected — ship via PR.
- Python ≥3.13; ruff `select = ["ALL"]` (ignores `D`, `COM812`, `ISC001`), line length 120. **No `assert` outside tests (S101), no function calls in argument defaults (B008), max 5 function args (PLR0913).**
- Coverage gate: `fail_under = 80` across `scale`, `hevy`, and (after Task 1) `nutrition`.
- `fetch.py` stays **outside** lint/type/test scope — glue there is deliberately untested; keep it dumb.
- No new runtime dependencies. `nutrition/` is stdlib-only.
- Constants live in `TargetConfig` (code, changed via PR — no env/yaml config).
- All InfluxDB queries must be time-bounded (unbounded scans hit the parquet query-file limit).
- Windows dev machine: run commands via `uv run ...` from the repo root.

---

### Task 1: `nutrition` package — `compute_target` (TDD) + tooling scope

**Files:**
- Create: `nutrition/__init__.py` (empty)
- Create: `nutrition/target.py`
- Create: `tests/test_calorie_target.py` (flat layout — repo convention, amends the spec's `tests/nutrition/` path)
- Modify: `pyproject.toml` (three scope lists)
- Modify: `.github/workflows/ci.yml` (three command lines)
- Modify: `CLAUDE.md` (the `## Commands` block only)

**Interfaces:**
- Consumes: nothing (pure stdlib).
- Produces (Task 2 relies on these exact names):
  - `nutrition.target.TargetConfig` — frozen dataclass, all-default constructor.
  - `nutrition.target.LastGood(target: float, maintenance: float)` — frozen dataclass.
  - `nutrition.target.TargetResult` — frozen dataclass with fields `status: Literal["ok","stale","clamped","bootstrapping"]`, `target: float | None`, `maintenance: float | None`, `intake_mean: float | None`, `weight_rate_kg_wk: float | None`, `logged_days: int`, `weighins: int`.
  - `nutrition.target.compute_target(intake: list[tuple[date, float]], weights: list[tuple[date, float]], last_good: LastGood | None, today: date, cfg: TargetConfig | None = None) -> TargetResult` — `intake` is daily kcal totals, `weights` is per-weigh-in `(calendar day, kg)` (duplicate days allowed).

- [ ] **Step 1: Create the empty package and write the failing tests**

Create `nutrition/__init__.py` as an empty file.

Create `tests/test_calorie_target.py` with exactly:

```python
from datetime import date, timedelta

from nutrition.target import LastGood, TargetConfig, compute_target

TODAY = date(2026, 7, 26)
LAST_GOOD = LastGood(target=2400.0, maintenance=2950.0)


def days_ago(n: int) -> date:
    return TODAY - timedelta(days=n)


def steady_intake(kcal: float = 2500.0, n: int = 14) -> list[tuple[date, float]]:
    """Daily totals for the n days before today (yesterday backwards)."""
    return [(days_ago(i), kcal) for i in range(1, n + 1)]


def linear_weights(start_kg: float = 94.0, kg_per_day: float = -0.05, n: int = 15) -> list[tuple[date, float]]:
    """n weigh-ins ending yesterday, changing by exactly kg_per_day per day."""
    return [(days_ago(i), start_kg + kg_per_day * (n - i)) for i in range(1, n + 1)]


def test_happy_path_known_answer():
    # 14 logged days at 2500, losing 0.05 kg/day ->
    # maintenance = 2500 + 0.05*7700 = 2885, target = 2885-550 = 2335 -> rounded 2340
    res = compute_target(steady_intake(), linear_weights(), None, TODAY)
    assert res.status == "ok"
    assert res.maintenance == 2885.0
    assert res.target == 2340.0
    assert res.intake_mean == 2500.0
    assert res.weight_rate_kg_wk == -0.35
    assert res.logged_days == 14
    assert res.weighins == 15


def test_intake_gaps_average_over_logged_days_only():
    # 10 logged days out of 21 -- missing days must not drag the mean down
    intake = [(days_ago(i), 2000.0) for i in (1, 3, 5, 7, 9, 11, 13, 15, 17, 19)]
    res = compute_target(intake, linear_weights(), None, TODAY)
    assert res.status == "ok"
    assert res.intake_mean == 2000.0
    assert res.logged_days == 10


def test_low_kcal_days_count_as_unlogged():
    # days under 800 kcal are half-logged noise: excluded from mean AND from the gate count
    intake = steady_intake(2000.0, 10) + [(days_ago(i), 400.0) for i in (11, 12, 13)]
    res = compute_target(intake, linear_weights(), None, TODAY)
    assert res.status == "ok"
    assert res.intake_mean == 2000.0
    assert res.logged_days == 10


def test_today_intake_excluded():
    # today's partial log must not enter the window
    res = compute_target([*steady_intake(), (TODAY, 300.0)], linear_weights(), None, TODAY)
    assert res.logged_days == 14
    assert res.intake_mean == 2500.0


def test_out_of_window_data_ignored():
    intake = [*steady_intake(), (days_ago(30), 9000.0)]
    weights = [*linear_weights(), (days_ago(40), 99.0)]
    res = compute_target(intake, weights, None, TODAY)
    assert res.status == "ok"
    assert res.intake_mean == 2500.0
    assert res.weighins == 15


def test_duplicate_same_day_weighins_collapse_to_daily_mean():
    weights = linear_weights()
    dup_day = weights[0][0]
    dup_kg = weights[0][1]
    res = compute_target(steady_intake(), [*weights, (dup_day, dup_kg)], None, TODAY)
    assert res.weighins == 15  # still 15 distinct days
    assert res.status == "ok"
    assert res.maintenance == 2885.0  # duplicate at same value must not move the slope


def test_gate_too_few_logged_days_freezes_to_last_good():
    res = compute_target(steady_intake(2500.0, 5), linear_weights(), LAST_GOOD, TODAY)
    assert res.status == "stale"
    assert res.target == 2400.0
    assert res.maintenance == 2950.0
    assert res.logged_days == 5


def test_gate_too_few_weighins_freezes():
    res = compute_target(steady_intake(), linear_weights(n=2), LAST_GOOD, TODAY)
    assert res.status == "stale"
    assert res.target == 2400.0
    assert res.weighins == 2


def test_gate_short_weighin_span_freezes():
    weights = [(days_ago(i), 94.0) for i in (1, 2, 3, 4)]  # span 3 days < 10
    res = compute_target(steady_intake(), weights, LAST_GOOD, TODAY)
    assert res.status == "stale"
    assert res.weighins == 4


def test_stale_still_reports_observables():
    # frozen target, but computable diagnostics are still surfaced
    res = compute_target(steady_intake(2500.0, 5), linear_weights(), LAST_GOOD, TODAY)
    assert res.status == "stale"
    assert res.intake_mean == 2500.0
    assert res.weight_rate_kg_wk == -0.35


def test_gate_failure_without_history_bootstraps():
    res = compute_target(steady_intake(2500.0, 5), linear_weights(), None, TODAY)
    assert res.status == "bootstrapping"
    assert res.target is None
    assert res.maintenance is None
    assert res.intake_mean is None
    assert res.weight_rate_kg_wk is None
    assert res.logged_days == 5
    assert res.weighins == 15


def test_empty_inputs_bootstrap():
    res = compute_target([], [], None, TODAY)
    assert res.status == "bootstrapping"
    assert res.logged_days == 0
    assert res.weighins == 0


def test_clamp_implausible_maintenance():
    # 5000 kcal/day while losing weight -> maintenance 5385 > 4500 -> clamped
    res = compute_target(steady_intake(5000.0), linear_weights(), LAST_GOOD, TODAY)
    assert res.status == "clamped"
    assert res.target == 2400.0
    assert res.maintenance == 2950.0


def test_clamp_implausible_rate():
    # losing 0.25 kg/day (1.75 kg/wk) is not fat loss -> clamped even though
    # maintenance (2500 + 1925 = 4425) is within bounds
    res = compute_target(steady_intake(), linear_weights(kg_per_day=-0.25), LAST_GOOD, TODAY)
    assert res.status == "clamped"
    assert res.target == 2400.0


def test_clamp_without_history_bootstraps():
    res = compute_target(steady_intake(5000.0), linear_weights(), None, TODAY)
    assert res.status == "bootstrapping"
    assert res.target is None


def test_target_floor():
    # 1900 in while gaining 0.01 kg/day -> maintenance 1823, raw target 1273 -> floored to 1500
    res = compute_target(steady_intake(1900.0), linear_weights(kg_per_day=0.01), None, TODAY)
    assert res.status == "ok"
    assert res.maintenance == 1823.0
    assert res.target == 1500.0


def test_config_overrides_apply():
    cfg = TargetConfig(deficit_kcal=0.0)
    res = compute_target(steady_intake(), linear_weights(), None, TODAY, cfg)
    assert res.status == "ok"
    assert res.target == 2890.0  # maintenance 2885 - 0, rounded to nearest 10
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_calorie_target.py -v --no-cov`
Expected: collection error — `ModuleNotFoundError: No module named 'nutrition.target'`.

- [ ] **Step 3: Implement `nutrition/target.py`**

Create `nutrition/target.py` with exactly:

```python
"""Adaptive calorie target: back-calculate maintenance from intake vs weight trend.

Community-consensus "adaptive TDEE" method (nSuns spreadsheet, Stronger by
Science, MacroFactor): over a trailing window,
    maintenance = mean(intake on logged days) - weight_slope_kg_day * kcal_per_kg
    target      = maintenance - deficit
Fault tolerance is the point: thin or implausible data freezes the target at
the last good value instead of recomputing. Full design:
docs/superpowers/specs/2026-07-05-adaptive-calorie-target-design.md
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

Status = Literal["ok", "stale", "clamped", "bootstrapping"]

_MIN_SLOPE_POINTS = 2  # a slope needs two points


@dataclass(frozen=True)
class TargetConfig:
    window_days: int = 21
    kcal_per_kg: float = 7700.0
    deficit_kcal: float = 550.0
    min_daily_kcal: float = 800.0  # below this a day counts as unlogged
    min_logged_days: int = 10
    min_weighins: int = 4
    min_weighin_span_days: int = 10
    maintenance_min: float = 1800.0
    maintenance_max: float = 4500.0
    max_rate_kg_day: float = 0.2
    target_floor: float = 1500.0


@dataclass(frozen=True)
class LastGood:
    target: float
    maintenance: float


@dataclass(frozen=True)
class TargetResult:
    status: Status
    target: float | None
    maintenance: float | None
    intake_mean: float | None
    weight_rate_kg_wk: float | None
    logged_days: int
    weighins: int


@dataclass(frozen=True)
class _Observed:
    intake_mean: float | None
    weight_rate_kg_wk: float | None
    logged_days: int
    weighins: int


def _daily_weight_means(weights: list[tuple[date, float]], window_start: date) -> dict[date, float]:
    by_day: dict[date, list[float]] = {}
    for day, kg in weights:
        if day >= window_start:
            by_day.setdefault(day, []).append(kg)
    return {day: sum(vals) / len(vals) for day, vals in by_day.items()}


def _ols_slope_kg_per_day(daily: dict[date, float], window_start: date) -> float:
    pairs = [(float((day - window_start).days), kg) for day, kg in daily.items()]
    n = len(pairs)
    if n < _MIN_SLOPE_POINTS:
        return 0.0
    mean_x = sum(x for x, _ in pairs) / n
    mean_y = sum(y for _, y in pairs) / n
    denom = sum((x - mean_x) ** 2 for x, _ in pairs)
    if denom == 0.0:
        return 0.0
    return sum((x - mean_x) * (y - mean_y) for x, y in pairs) / denom


def _fallback(status: Status, last_good: LastGood | None, obs: _Observed) -> TargetResult:
    if last_good is None:
        # bootstrapping: no history to freeze to -- report counts only
        return TargetResult("bootstrapping", None, None, None, None, obs.logged_days, obs.weighins)
    return TargetResult(
        status,
        last_good.target,
        last_good.maintenance,
        obs.intake_mean,
        obs.weight_rate_kg_wk,
        obs.logged_days,
        obs.weighins,
    )


def compute_target(
    intake: list[tuple[date, float]],
    weights: list[tuple[date, float]],
    last_good: LastGood | None,
    today: date,
    cfg: TargetConfig | None = None,
) -> TargetResult:
    """Compute today's target from trailing intake (daily kcal totals) and weigh-ins (kg)."""
    cfg = cfg or TargetConfig()
    window_start = today - timedelta(days=cfg.window_days)

    # Intake: logged days only, today excluded (partial day)
    by_day = {day: kcal for day, kcal in intake if window_start <= day < today}
    logged = {day: kcal for day, kcal in by_day.items() if kcal >= cfg.min_daily_kcal}
    logged_days = len(logged)
    intake_mean = round(sum(logged.values()) / logged_days, 1) if logged_days else None

    # Weight: daily means (dup weigh-ins collapse), today included, OLS slope
    daily_weights = _daily_weight_means(weights, window_start)
    weighins = len(daily_weights)
    span_days = (max(daily_weights) - min(daily_weights)).days if daily_weights else 0
    slope = _ols_slope_kg_per_day(daily_weights, window_start) if weighins >= _MIN_SLOPE_POINTS else None
    rate_kg_wk = round(slope * 7, 2) if slope is not None else None

    obs = _Observed(intake_mean, rate_kg_wk, logged_days, weighins)

    gate_ok = (
        logged_days >= cfg.min_logged_days
        and weighins >= cfg.min_weighins
        and span_days >= cfg.min_weighin_span_days
    )
    if not gate_ok or intake_mean is None or slope is None:
        return _fallback("stale", last_good, obs)

    maintenance = round(intake_mean - slope * cfg.kcal_per_kg, 1)
    if not (cfg.maintenance_min <= maintenance <= cfg.maintenance_max) or abs(slope) > cfg.max_rate_kg_day:
        return _fallback("clamped", last_good, obs)

    target = max(maintenance - cfg.deficit_kcal, cfg.target_floor)
    # half-up to nearest 10 (round() is banker's: round(288.5) == 288 would jitter targets)
    target = float(int(target / 10.0 + 0.5) * 10)
    return TargetResult("ok", target, maintenance, intake_mean, rate_kg_wk, logged_days, weighins)
```

- [ ] **Step 4: Run the tests, verify all pass**

Run: `uv run pytest tests/test_calorie_target.py -v --no-cov`
Expected: 17 passed. If a known-answer assert fails by a rounding hair, fix the implementation (the test values are the contract), not the test.

- [ ] **Step 5: Add `nutrition` to the tooling scopes**

In `pyproject.toml` make these three edits:

```toml
# [tool.ruff]  src line becomes:
src = ["scale", "hevy", "nutrition", "tests"]

# [tool.coverage.run]  source line becomes:
source = ["scale", "hevy", "nutrition"]

# [tool.hatch.build.targets.wheel]  packages line becomes:
packages = ["scale", "hevy", "nutrition"]
```

In `.github/workflows/ci.yml`, update the three scoped commands:

```yaml
      - run: uv run ruff check scale/ hevy/ nutrition/ tests/
      - run: uv run ruff format --check scale/ hevy/ nutrition/ tests/
```
and
```yaml
      - run: uv run ty check scale/ hevy/ nutrition/
```

In `CLAUDE.md`, update the `## Commands` block to match:

```bash
uv run ruff check scale/ hevy/ nutrition/ tests/
uv run ruff format --check scale/ hevy/ nutrition/ tests/
uv run ty check scale/ hevy/ nutrition/
```
(leave the `uv sync` and `pytest` lines as they are)

- [ ] **Step 6: Run the full local gate**

Run, from repo root:
```bash
uv run ruff check scale/ hevy/ nutrition/ tests/
uv run ruff format scale/ hevy/ nutrition/ tests/
uv run ty check scale/ hevy/ nutrition/
uv run pytest --cov -v
```
Expected: ruff clean (format may rewrite `nutrition/target.py` / the test file — that's fine, keep the result), ty clean, all tests pass, total coverage ≥80%. If ruff flags a rule in `nutrition/` not anticipated here, fix the code to satisfy it (do not add file-level ignores without a comment explaining why).

- [ ] **Step 7: Commit**

```bash
git add nutrition/ tests/test_calorie_target.py pyproject.toml .github/workflows/ci.yml CLAUDE.md
git commit -m "feat: nutrition package with adaptive calorie target computation"
```

---

### Task 2: `fetch.py` glue + Dockerfile

**Files:**
- Modify: `fetch.py` (one import; two new functions after `write_to_influx`, before the `# --- Main loop ---` comment; one hook at the end of `run_once`)
- Modify: `Dockerfile` (copy the package into the image)

**Interfaces:**
- Consumes: `nutrition.target.compute_target`, `LastGood` (exact signatures in Task 1); existing `fetch.py` helpers `pt(measurement, ts, fields)`, `write_to_influx(cfg, points)`, `load_config()` keys `influx_url` / `influx_token` / `influx_database`.
- Produces: `update_calorie_target(cfg: dict) -> None` called once per `run_once`; the `calorie_target` measurement (fields `status` str, `logged_days`/`weighins`/`target`/`maintenance`/`intake_mean`/`weight_rate_kg_wk` numeric — Nones dropped by `write_to_influx`; the shared writer adds the standard `Device` tag and stores ints as floats).

- [ ] **Step 1: Add the import**

In `fetch.py`, directly after `from influxdb_client_3 import InfluxDBClient3, Point` add:

```python
from nutrition.target import LastGood, compute_target
```

- [ ] **Step 2: Add the glue functions**

Insert after the `write_to_influx` function (before the `# --- Main loop ---` comment):

```python
# --- Adaptive calorie target (design: docs/superpowers/specs/2026-07-05-adaptive-calorie-target-design.md) ---


def _influx_rows(client, sql: str) -> list:
    try:
        return client.query(sql).to_pylist()
    except Exception as e:  # table may not exist yet (first run)
        log.warning("calorie-target query failed: %s", e)
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
    client = InfluxDBClient3(
        host=cfg["influx_url"],
        token=cfg["influx_token"],
        database=cfg["influx_database"],
    )
    try:
        intake_rows = _influx_rows(
            client, "SELECT time, \"caloriesIn\" FROM \"Nutrition\" WHERE time >= now() - interval '22 days'"
        )
        weight_rows = _influx_rows(
            client, "SELECT time, \"weight\" FROM \"body_composition\" WHERE time >= now() - interval '22 days'"
        )
        last_rows = _influx_rows(
            client,
            "SELECT \"target\", \"maintenance\" FROM \"calorie_target\" "
            "WHERE \"status\" = 'ok' AND time >= now() - interval '90 days' ORDER BY time DESC LIMIT 1",
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
```

- [ ] **Step 3: Hook into `run_once`**

At the end of `run_once`, after the `log.info("Sync complete: ...")` line, add:

```python
    try:
        update_calorie_target(cfg)
    except Exception:
        log.exception("Calorie target update failed (sync unaffected)")
```

- [ ] **Step 4: Add the package to the Docker image**

In `Dockerfile`, after `COPY fetch.py .` add:

```dockerfile
COPY nutrition/ nutrition/
```

- [ ] **Step 5: Syntax-verify fetch.py**

Run: `uv run python -m py_compile fetch.py && echo OK`
Expected: `OK`. (fetch.py is outside lint/test scope; real verification happens at deploy, Task 5.)

- [ ] **Step 6: Commit**

```bash
git add fetch.py Dockerfile
git commit -m "feat: compute and write calorie_target each sync cycle"
```

---

### Task 3: Grafana dashboard panels

**Files:**
- Modify: `dashboard.json` — top-level shape is `{"dashboard": {...}, "overwrite": true}`; panels live at `.dashboard.panels`. Replace panels id 3 and id 51 wholesale; append one new panel id 63.

**Interfaces:**
- Consumes: the `calorie_target` measurement (Task 2), InfluxQL via datasource uid `P9A8567EC67EE4A5C`.
- Produces: panels "Daily Calorie Target" (id 51), "Intake vs Target" (id 3), "Est. Energy Out (watch)" (id 63).

- [ ] **Step 1: Replace panel id 51 ("7d Avg Net" → "Daily Calorie Target" stat)**

Find the object in `.dashboard.panels` with `"id": 51` and replace it entirely with:

```json
{
  "id": 51,
  "title": "Daily Calorie Target",
  "type": "stat",
  "description": "Adaptive target = maintenance − 550 kcal (≈0.5 kg/week cut). Maintenance is back-calculated from 21d of logged intake vs weight trend (adaptive TDEE); the watch's calories-out is never used. Status: live = fresh; stale/clamped = frozen at last good value; collecting data = not enough history yet (needs 10 logged days + 4 weigh-ins).",
  "gridPos": {"h": 8, "w": 4, "x": 0, "y": 8},
  "datasource": {"type": "influxdb", "uid": "P9A8567EC67EE4A5C"},
  "fieldConfig": {
    "defaults": {
      "unit": "kcal",
      "decimals": 0,
      "noValue": "collecting data",
      "color": {"mode": "fixed", "fixedColor": "green"}
    },
    "overrides": [
      {
        "matcher": {"id": "byName", "options": "Trend"},
        "properties": [
          {"id": "unit", "value": "none"},
          {"id": "decimals", "value": 2},
          {"id": "displayName", "value": "kg/wk"},
          {"id": "color", "value": {"mode": "fixed", "fixedColor": "text"}}
        ]
      },
      {
        "matcher": {"id": "byName", "options": "Status"},
        "properties": [
          {
            "id": "mappings",
            "value": [
              {
                "type": "value",
                "options": {
                  "ok": {"text": "live", "color": "green", "index": 0},
                  "stale": {"text": "stale — log food + weigh-ins", "color": "yellow", "index": 1},
                  "clamped": {"text": "clamped — implausible data", "color": "orange", "index": 2},
                  "bootstrapping": {"text": "collecting data", "color": "blue", "index": 3}
                }
              }
            ]
          }
        ]
      }
    ]
  },
  "options": {
    "graphMode": "none",
    "textMode": "value_and_name",
    "colorMode": "value",
    "reduceOptions": {"calcs": ["lastNotNull"]}
  },
  "targets": [
    {
      "refId": "A",
      "rawQuery": true,
      "resultFormat": "time_series",
      "alias": "Target",
      "datasource": {"type": "influxdb", "uid": "P9A8567EC67EE4A5C"},
      "query": "SELECT last(\"target\") FROM \"calorie_target\" WHERE time > now() - 3d"
    },
    {
      "refId": "B",
      "rawQuery": true,
      "resultFormat": "time_series",
      "alias": "Trend",
      "datasource": {"type": "influxdb", "uid": "P9A8567EC67EE4A5C"},
      "query": "SELECT last(\"weight_rate_kg_wk\") FROM \"calorie_target\" WHERE time > now() - 3d"
    },
    {
      "refId": "C",
      "rawQuery": true,
      "resultFormat": "table",
      "datasource": {"type": "influxdb", "uid": "P9A8567EC67EE4A5C"},
      "query": "SELECT last(\"status\") AS \"Status\" FROM \"calorie_target\" WHERE time > now() - 3d"
    }
  ]
}
```

- [ ] **Step 2: Replace panel id 3 ("Energy Balance" → "Intake vs Target")**

Find the object with `"id": 3` and replace it entirely (note `w` shrinks 20→12 to make room for the new panel; the `Net`/`Baseline`/`Active`/`Total` targets, overrides, and both `transformations` are gone):

```json
{
  "id": 3,
  "title": "Intake vs Target",
  "type": "timeseries",
  "description": "Daily kcal logged (MFP) vs the adaptive target and back-calculated maintenance. Watch calories-out is excluded — 20–90% error.",
  "gridPos": {"h": 8, "w": 12, "x": 4, "y": 8},
  "datasource": {"type": "influxdb", "uid": "P9A8567EC67EE4A5C"},
  "fieldConfig": {
    "defaults": {
      "unit": "kcal",
      "decimals": 0,
      "custom": {
        "drawStyle": "line",
        "lineWidth": 2,
        "fillOpacity": 0,
        "spanNulls": true,
        "showPoints": "auto",
        "pointSize": 6,
        "barMaxWidth": 25
      }
    },
    "overrides": [
      {
        "matcher": {"id": "byName", "options": "In"},
        "properties": [
          {"id": "color", "value": {"mode": "fixed", "fixedColor": "light-blue"}},
          {"id": "custom.drawStyle", "value": "bars"},
          {"id": "custom.fillOpacity", "value": 45},
          {"id": "custom.lineWidth", "value": 0}
        ]
      },
      {
        "matcher": {"id": "byName", "options": "Target"},
        "properties": [
          {"id": "color", "value": {"mode": "fixed", "fixedColor": "green"}}
        ]
      },
      {
        "matcher": {"id": "byName", "options": "Maintenance"},
        "properties": [
          {"id": "color", "value": {"mode": "fixed", "fixedColor": "orange"}},
          {"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [8, 6]}}
        ]
      }
    ]
  },
  "targets": [
    {
      "refId": "A",
      "rawQuery": true,
      "resultFormat": "time_series",
      "alias": "In",
      "datasource": {"type": "influxdb", "uid": "P9A8567EC67EE4A5C"},
      "query": "SELECT max(\"caloriesIn\") FROM \"Nutrition\" WHERE $timeFilter GROUP BY time(1d) fill(none)"
    },
    {
      "refId": "B",
      "rawQuery": true,
      "resultFormat": "time_series",
      "alias": "Target",
      "datasource": {"type": "influxdb", "uid": "P9A8567EC67EE4A5C"},
      "query": "SELECT last(\"target\") FROM \"calorie_target\" WHERE $timeFilter GROUP BY time(1d) fill(previous)"
    },
    {
      "refId": "C",
      "rawQuery": true,
      "resultFormat": "time_series",
      "alias": "Maintenance",
      "datasource": {"type": "influxdb", "uid": "P9A8567EC67EE4A5C"},
      "query": "SELECT last(\"maintenance\") FROM \"calorie_target\" WHERE $timeFilter GROUP BY time(1d) fill(previous)"
    }
  ]
}
```

- [ ] **Step 3: Append new panel id 63 ("Est. Energy Out (watch)")**

Append to `.dashboard.panels` (fills the freed x:16–24 slot on the same row):

```json
{
  "id": 63,
  "title": "Est. Energy Out (watch)",
  "type": "timeseries",
  "description": "Wearable energy-expenditure estimate — 20–90% error in studies, worst for lifting. Context only; never used for targets.",
  "gridPos": {"h": 8, "w": 8, "x": 16, "y": 8},
  "datasource": {"type": "influxdb", "uid": "P9A8567EC67EE4A5C"},
  "fieldConfig": {
    "defaults": {
      "unit": "kcal",
      "decimals": 0,
      "custom": {"drawStyle": "line", "lineWidth": 1, "fillOpacity": 10, "spanNulls": false}
    },
    "overrides": [
      {
        "matcher": {"id": "byName", "options": "Total"},
        "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "dark-blue"}}]
      },
      {
        "matcher": {"id": "byName", "options": "Active"},
        "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "orange"}}]
      }
    ]
  },
  "targets": [
    {
      "refId": "A",
      "rawQuery": true,
      "resultFormat": "time_series",
      "alias": "Total",
      "datasource": {"type": "influxdb", "uid": "P9A8567EC67EE4A5C"},
      "query": "SELECT max(\"value\") FROM \"calories\" WHERE $timeFilter GROUP BY time(1d) fill(none)"
    },
    {
      "refId": "B",
      "rawQuery": true,
      "resultFormat": "time_series",
      "alias": "Active",
      "datasource": {"type": "influxdb", "uid": "P9A8567EC67EE4A5C"},
      "query": "SELECT max(\"active\") FROM \"calories\" WHERE $timeFilter GROUP BY time(1d) fill(none)"
    }
  ]
}
```

- [ ] **Step 4: Validate the JSON**

Run:
```bash
python -c "
import json
d = json.load(open('dashboard.json'))['dashboard']
panels = {p['id']: p['title'] for p in d['panels']}
assert panels[51] == 'Daily Calorie Target', panels[51]
assert panels[3] == 'Intake vs Target', panels[3]
assert panels[63] == 'Est. Energy Out (watch)', panels[63]
assert len(panels) == 21, len(panels)
bad = [p['id'] for p in d['panels'] if p['id'] in (3, 51) and p.get('transformations')]
assert not bad, f'leftover transformations on {bad}'
print('dashboard.json OK:', len(panels), 'panels')
"
```
Expected: `dashboard.json OK: 21 panels`

- [ ] **Step 5: Commit**

```bash
git add dashboard.json
git commit -m "feat: calorie target panels; demote watch calories-out to estimate-only"
```

---

### Task 4: Docs — CLAUDE.md, health-checkpoint skill, spec amendment

**Files:**
- Modify: `CLAUDE.md` (components table + gotchas; the Commands block was already done in Task 1)
- Modify: `.claude/skills/health-checkpoint/SKILL.md` (targets table + measurement cheat sheet + section 2 of "What to produce")
- Modify: `docs/superpowers/specs/2026-07-05-adaptive-calorie-target-design.md` (two implementation-reality notes)

**Interfaces:** documentation only; must match Task 1/2 names exactly (`calorie_target`, `status` values, `nutrition/` package, `update_calorie_target`).

- [ ] **Step 1: CLAUDE.md components table**

Add a row to the `## Components` table:

```markdown
| Adaptive calorie target | `nutrition/` package + `update_calorie_target` in `fetch.py` | inside the `health-fetch` sync cycle | `calorie_target` measurement |
```

- [ ] **Step 2: CLAUDE.md gotchas**

Add one bullet to `## Gotchas`:

```markdown
- `calorie_target` is the adaptive daily kcal target (maintenance − 550), back-calculated
  from 21d of logged intake vs weight trend (adaptive-TDEE method). Its `status` field is
  the truth marker: `ok` | `stale` | `clamped` (both freeze the last good target rather
  than recompute from thin/implausible data) | `bootstrapping` (no target yet). Days under
  800 kcal count as unlogged. The watch's calories-out (`calories` measurement) has 20–90%
  error and must never feed targets — it's displayed as estimate-only.
```

- [ ] **Step 3: health-checkpoint skill updates**

In `.claude/skills/health-checkpoint/SKILL.md`:

a) Add a row to the "Leo's current targets" table:

```markdown
| Calories | Read the `calorie_target` measurement — `target` field (= maintenance − 550, adaptive). Honour `status`: ok = live; stale/clamped = frozen last-good; bootstrapping = no target yet, don't invent one. |
```

b) Add a row to the measurement cheat sheet table:

```markdown
| `calorie_target` | `target`, `maintenance`, `intake_mean`, `weight_rate_kg_wk`, `logged_days`, `weighins`, `status` | Adaptive target written each fetch cycle. `status` is the truth marker — see targets table. |
```

c) In "What to produce" item 2 (Protein), append to the end of the sentence about average on logged days:

```markdown
Also compare average intake on logged days to the current `calorie_target` (over/under and by how much).
```

- [ ] **Step 4: Spec amendment (implementation realities)**

In `docs/superpowers/specs/2026-07-05-adaptive-calorie-target-design.md`, at the end of the "Measurement schema" section add:

```markdown
**Implementation notes (2026-07-05):** points flow through the shared
`write_to_influx`, so they carry the standard `Device` tag (spec originally
said tag-less) and integer fields are stored as floats. Tests live flat at
`tests/test_calorie_target.py` per repo convention rather than
`tests/nutrition/`.
```

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md .claude/skills/health-checkpoint/SKILL.md docs/superpowers/specs/2026-07-05-adaptive-calorie-target-design.md
git commit -m "docs: calorie target in CLAUDE.md, health-checkpoint skill, spec notes"
```

---

### Task 5: PR, deploy to the Pi, verify end-to-end

**Files:** none (operations). Requires: Leo available for the PR-merge decision and the Grafana admin password.

- [ ] **Step 1: Push and open the PR**

```bash
git push -u origin feat/adaptive-calorie-target
gh pr create --title "feat: adaptive calorie target (data-driven maintenance, watch cal-out demoted)" --body "Implements docs/superpowers/specs/2026-07-05-adaptive-calorie-target-design.md: tested nutrition/ package (adaptive-TDEE back-calculation with freeze gate + clamps), calorie_target measurement written each fetch cycle, Grafana panels (target stat, intake-vs-target, watch cal-out demoted to estimate-only), docs + skill updated."
```

- [ ] **Step 2: STOP — ask Leo to review/merge the PR**

Do not merge without his go-ahead. After merge: `git checkout main && git pull`.

- [ ] **Step 3: Deploy to the Pi**

```bash
ssh pi "cd ~/documents/code/raspberrypi/google-health-grafana && git pull --ff-only && docker compose up -d --build health-fetch"
```
Expected: image rebuilds (now includes `COPY nutrition/`), container recreated.

- [ ] **Step 4: Verify the fetcher computes and writes**

```bash
ssh pi 'sleep 45; docker logs health-fetch 2>&1 | grep -E "Calorie target|calorie-target" | tail -3'
```
Expected: a `Calorie target: None kcal (bootstrapping, 0 logged days, N weigh-ins)` line — with current data (intake history only starts today and today is excluded; no prior `ok` point) **bootstrapping is the correct result**. A debug-level `calorie-target query failed` message recurs each cycle until the first `ok` point exists (the last-good query references columns bootstrapping points don't write) — expected, self-heals. Then:

```bash
ssh pi 'curl -s -G "http://localhost:8181/query" --data-urlencode "db=health" --data-urlencode "q=SELECT * FROM \"calorie_target\" WHERE time >= now() - 1d"'
```
Expected: one point with `status=bootstrapping`, `logged_days=0`, `weighins` ≥ 3, no `target`/`maintenance` fields.

- [ ] **Step 5: Deploy the dashboard**

Ask Leo for the Grafana admin password (Pi-local), then:

```bash
scp dashboard.json pi:/tmp/dashboard.json
ssh pi 'GIP=$(docker inspect -f "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}" grafana); curl -s -X POST "http://$GIP:3000/api/dashboards/db" -H "Content-Type: application/json" -u "admin:<PASSWORD>" -d @/tmp/dashboard.json'
```
Expected: `{"id":...,"slug":"health","status":"success",...}`.

- [ ] **Step 6: Verify the panels render**

```bash
ssh pi 'GIP=$(docker inspect -f "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}" grafana); curl -s "http://$GIP:3000/api/dashboards/uid/google-health" | python3 -c "import json,sys; d=json.load(sys.stdin)[\"dashboard\"]; print([p[\"title\"] for p in d[\"panels\"] if p[\"id\"] in (3,51,63)])"'
```
Expected: `['Intake vs Target', 'Daily Calorie Target', 'Est. Energy Out (watch)']`. Then ask Leo to eyeball the dashboard: the stat should read "collecting data" (blue) with weigh-in trend, Intake vs Target shows intake bars (target line appears once out of bootstrapping), Est. Energy Out shows the watch series. If the Status text box misrenders (string stats can be finicky), fall back to removing target query C from panel 51 and rely on `noValue` — then re-POST.

- [ ] **Step 7: Clean up and report**

```bash
ssh pi 'rm /tmp/dashboard.json'
```
Report to Leo: deployed state, current status (`bootstrapping`, expected to flip to `ok` after ~10 logged days + 4 weigh-ins spanning 10 days), and that the target will first appear around mid-July if he logs daily.
