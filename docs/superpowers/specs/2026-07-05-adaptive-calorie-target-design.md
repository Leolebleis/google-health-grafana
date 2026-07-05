# Adaptive Calorie Target — Design

**Date:** 2026-07-05 · **Status:** approved pending review

## Problem

The dashboard has no calorie target, and the obvious candidate input — the
watch's "calories out" — is unreliable: wrist wearables show 20–90% energy-
expenditure error (Stanford: best device 27% median error, none under 20%),
worst for resistance training. Meanwhile Leo's real maintenance is knowable
from his own data: the community-consensus "adaptive TDEE" method (nSuns
spreadsheet, Stronger by Science, MacroFactor) back-calculates it from logged
intake vs bodyweight trend. Leo has both streams in InfluxDB (`Nutrition`,
`body_composition`) and has committed to daily logging.

## Decisions

| Decision | Choice |
|---|---|
| Where the math lives | New tested Python package `nutrition/` (approach B); `fetch.py` stays a thin untested orchestrator |
| Method | Equal-weight trailing window (nSuns-style), OLS slope for weight trend — not EWMA |
| Wearable calories-out | Dropped from all targets; kept as a small "estimate only" context panel |
| Output | New `calorie_target` InfluxDB measurement; Grafana + health-checkpoint skill both read it (one source of truth) |
| Fault tolerance | Data-sufficiency gate + plausibility clamps; on failure the target **freezes at the last good value** and is flagged, never recomputed from thin data |

## Non-goals

- EWMA / MacroFactor-style weighting (equal-weight is transparent and testable; revisit later if wanted)
- Macro (protein/carb/fat) targets — kcal only; protein target stays in the checkpoint skill
- Backfilling historical `calorie_target` points
- Runtime config (env/yaml) for constants — they are code constants, changed via PR

## Architecture

```
fetch.py run_once (existing 5-min cycle)
  └─ NEW step, wrapped in try/except (failure logs a warning, never breaks the sync):
       1. reader (fetch.py): 3 time-bounded InfluxDB queries via the existing client
            - Nutrition.caloriesIn, trailing 21d
            - body_composition.weight, trailing 21d
            - most recent calorie_target with status='ok' (≤90d back)
       2. nutrition/target.py: compute_target(intake, weights, last_good, cfg) -> TargetResult   [pure, tested]
       3. writer (fetch.py): write one calorie_target point at today's 00:00 UTC (overwrites per cycle)
```

Why the reader queries InfluxDB rather than reusing in-memory fetch data: after
the first sync, `fetch.py` fetches only a 2-day incremental API window, and it
never sees the scale's `body_composition` points at all. The DB is the one
complete store of both inputs.

Weight source is **`body_composition.weight` only** (the scale is the primary
weight path). The fitbit-schema `weight` measurement duplicates the same
weigh-ins via Google Health and is not mixed in.

## Algorithm (codified)

Constants (dataclass `TargetConfig`, defaults):

| Constant | Value | Basis |
|---|---|---|
| `window_days` | 21 | consensus 2–4 wk |
| `kcal_per_kg` | 7700 | standard |
| `deficit_kcal` | 550 | ~0.5 kg/wk cut |
| `min_daily_kcal` | 800 | engineering guard: below this a day counts as *unlogged* (half-logged days poison the mean) |
| `min_logged_days` | 10 | gate |
| `min_weighins` / `min_weighin_span_days` | 4 / 10 | gate: slope needs spread |
| `maintenance_bounds` | 1800–4500 kcal | clamp |
| `max_rate_kg_day` | 0.2 | clamp (1.4 kg/wk is implausible as fat) |
| `target_floor` | 1500 | final floor |

Steps, over the trailing 21-day window:

1. **Logged days:** dates in `[today−21d, today)` with `caloriesIn ≥ 800` —
   **today is excluded** (partial day).
   `intake_mean = mean(caloriesIn over logged days)`.
2. **Weight trend:** weigh-ins in `[today−21d, now]` — **today's weigh-in is
   included** (a morning weigh-in is complete data). Collapse to a daily mean per calendar day
   (guards duplicate writes), then OLS least-squares slope of kg vs day →
   `slope` (kg/day). Regression tolerates irregular spacing and gaps.
3. **Maintenance:** `maintenance = intake_mean − slope × 7700`
   (losing 0.05 kg/day on 2500 kcal → 2885).
4. **Target:** `target = max(maintenance − 550, 1500)`, rounded to nearest 10.
5. **Gate & clamps → status:**
   - `ok` — logged_days ≥ 10 AND weigh-ins ≥ 4 spanning ≥ 10 days AND maintenance ∈ [1800, 4500] AND |slope| ≤ 0.2
   - `stale` — gate failed (e.g. holiday gap) → carry last-good `target`/`maintenance`
   - `clamped` — gate passed but clamp failed → carry last-good values
   - `bootstrapping` — stale/clamped but no last-good exists → no target/maintenance fields written

The rolling window makes recovery automatic: gaps age out, no state to repair.

## Measurement schema — `calorie_target`

Tag-less, one point/day at 00:00 UTC (last write wins within a day).
Fields: `target` (f), `maintenance` (f), `intake_mean` (f),
`weight_rate_kg_wk` (f, = slope×7), `logged_days` (i), `weighins` (i),
`status` (s). Field presence by status:

- `ok` — all fields, freshly computed.
- `stale` / `clamped` — `target` and `maintenance` are **carried** from the
  last-good point; `intake_mean` / `weight_rate_kg_wk` are written only if they
  were computable this cycle (debuggability). `status` is the truth marker.
- `bootstrapping` — only `status`, `logged_days`, `weighins`.

**Implementation notes (2026-07-05):** points flow through the shared
`write_to_influx`, so they carry the standard `Device` tag (spec originally
said tag-less) and integer fields are stored as floats. Tests live flat at
`tests/test_calorie_target.py` per repo convention rather than
`tests/nutrition/`.

## Grafana changes (`dashboard.json`, deployed via `POST /api/dashboards/db`)

1. **New stat panel "Daily Calorie Target":** last `target` big; `maintenance`,
   `weight_rate_kg_wk` secondary; background colour by `status` value mapping
   (ok=green, stale=yellow, clamped=orange, bootstrapping=blue "collecting
   data"). Query time-bounded to `now()-3d`, independent of dashboard range.
2. **Repurpose the existing "Calories" panel → "Intake vs Target":** keep `In`
   daily bars (`Nutrition.caloriesIn`), add `target` and `maintenance` lines
   (`GROUP BY time(1d) fill(previous)`), remove `Total`/`Active`/`Baseline`
   series and the `Net` transform (they used the untrusted watch number).
3. **New small panel "Est. energy out (watch — estimate only)":** daily
   `max(calories.value)` + `active`, description stating it is unreliable and
   excluded from all targets.

## Code layout & testing

```
nutrition/__init__.py
nutrition/target.py      # TargetConfig, TargetResult, compute_target() — pure
tests/nutrition/test_target.py
```

`nutrition/` joins the lint/type/test scope (`ruff check`, `ruff format`,
`ty check`, pytest coverage ≥80): update `pyproject.toml` if scoping needs it,
`.github/workflows/ci.yml`, and the CLAUDE.md command lines. The InfluxDB
reader/writer glue stays in `fetch.py` (deliberately untested, kept dumb).

Test cases: known-answer happy path; logged-day averaging with gaps; `<800`
kcal days excluded; today excluded; slope correctness on irregular spacing and
duplicate same-day weigh-ins; each gate failure → stale carrying last-good;
no last-good → bootstrapping; both clamps; target floor; rounding.

## Docs

- CLAUDE.md: components table row, command scopes, gotcha line (`calorie_target`
  status semantics; calories-out is estimate-only, never target input).
- health-checkpoint skill: add `calorie_target` to the cheat sheet and a
  calorie row to the targets table (read the measurement, honour `status`).

## Deployment

1. PR (spec + implementation) → merge → Pi `git pull` → `docker compose up -d --build health-fetch`.
2. Wait one cycle; verify `calorie_target` exists via the v1 `/query` endpoint (expect `bootstrapping` until ~10 logged days accrue).
3. POST updated `dashboard.json` to Grafana (admin, container pi-net IP); verify panels.
