---
name: health-checkpoint
description: Use when Leo asks how he's doing / how he's tracking / for a health, fitness, or training checkpoint / a weekly (or longer) review — covers workout progression (sets/reps/weights over time), protein consistency, weight-vs-goal, and recovery markers. Pulls from InfluxDB on the Pi + the Hevy MCP.
---

# Health Checkpoint

## Overview

Produce a health/fitness checkpoint over a period (default the last 7 days, but
accept any range Leo asks for — "this month", "since June", etc.). Answer one
question: **how is Leo's data evolving?** Four axes: training progression,
protein, weight-vs-goal, recovery.

Data comes from two sources: **InfluxDB 3** on the Pi (health/activity/scale +
nutrition) and the **Hevy MCP** (set-by-set workout detail). Both are needed —
neither alone gives the full picture.

## THE trap — read this first

**Sessions trained ≠ Hevy workout count.** These are different measurements and
conflating them produces a flat-wrong report (an earlier run claimed "1 workout
in 3 weeks" when Leo had trained 4× that week).

- **Count of training sessions** → `Activity Records` where `activityName = 'WEIGHTLIFTING'` (logged automatically by the watch, catches every session). **Count distinct *days*, not rows** — a session can double-log (e.g. both `WEIGHTLIFTING` and `STRENGTH_TRAINING`, or two records seconds apart). `GROUP BY time(1d) fill(0)` and count days with any record. If a checkpoint looks light, also check `activityName = 'STRENGTH_TRAINING'` in case a session logged only under that name.
- **Set/rep/weight detail** → Hevy (`get-workouts` / `get-exercise-history`) and the `workout` / `workout_set` InfluxDB measurements.

Leo started Hevy on **2026-07-05** (account `termiduck`). So **set-level history
only exists from that date forward.** Before it, the watch knows a session
happened but there are no sets. Never report "he only trained N times" off the
Hevy count — always cross-check against `Activity Records`.

## Leo's current targets (update here when they change)

| Axis | Target |
|------|--------|
| Weight | Slow cut, ~**0.5 kg/week down** from 94 kg. Rate-based, no fixed endpoint — judge the *trend slope*, not distance to a number. |
| Protein | **180 g/day** |
| Side-delt volume | **12–18 sets/week** (lateral raises are on every training day — "non-negotiable") |
| Back volume | **10–16 sets/week** |
| Split | 4-day (Day 1 Shoulders & Back Width · Day 2 Lower, hip-friendly · Day 3 Shoulders & Chest · Day 4 Back & Arms) + 3-day full-body fallback. Program folder: "Wider Shoulders". |
| Constraints | **Protect left hip** on lower days (limited depth, neutral stance). Adherence is his self-identified weak point — call it out honestly. |

## Querying InfluxDB (v1 endpoint via the Pi)

Run over SSH. Double-quote every identifier, always time-bound, group daily in
`Europe/London`. Write the query to a file first to dodge nested-quote hell:

```bash
ssh pi 'cat > /tmp/hc.sql <<"EOF"
SELECT max("value") FROM "Total Steps" WHERE time >= '\''2026-06-29T00:00:00Z'\'' GROUP BY time(1d) tz('\''Europe/London'\'')
EOF
curl -s -G "http://localhost:8181/query" --data-urlencode "db=health" --data-urlencode "q@/tmp/hc.sql"'
```

Multiple statements in one call: separate with `;`. Add `--data-urlencode "epoch=s"`
if you want unix timestamps. Set the start time from the requested period.

### Measurement / field cheat sheet

| Measurement | Fields (use) | Notes |
|-------------|--------------|-------|
| `Activity Records` | `activityName` (tag-like string), `duration`, `calories` | **Session counter.** Filter `activityName='WEIGHTLIFTING'`. duration/calories often 0. |
| `workout` | `duration_min`, `volume_kg`, `set_count`, `exercise_count`, `workout_id` | Per-session Hevy aggregates. Only from 2026-07-05. |
| `workout_set` | per-set rows | Hevy set detail mirror. Only from 2026-07-05. |
| `Nutrition` | `protein`, `caloriesIn`, `carbs`, `fat` | Daily. Only present on days Leo logs food (sparse — he logs inconsistently). |
| `body_composition` | `weight`, `body_fat_pct`, `muscle_mass`, `bmi`, `visceral_fat`, ... | Xiaomi S400. Arrives when the phone syncs — not every day. |
| `weight` | `value` | Separate simple weight series (fitbit-schema). |
| `Total Steps` | `value` | Daily total — use `max()` per day, not `sum()`. |
| `RestingHR` | `value` | Daily. |
| `HRV` | `dailyRmssd` | Daily. |
| `Sleep Summary` | `minutesAsleep`, `minutesDeep`, `minutesREM`, `minutesAwake` | **Duplicated across sources + includes naps** — treat as approximate, don't over-precision it. |
| `Activity Minutes` | `minutesVeryActive`, `minutesFairlyActive`, `minutesLightlyActive` | Daily. |
| `calories` | `value` (total out), `active` | Daily. |
| `RestingHR`,`SPO2`,`BreathingRate` | — | Extra recovery signals if asked. |

## Querying Hevy (workout progression)

- `get-workouts` (paginate) — sessions in the period with full sets.
- `get-exercise-history` (by `exerciseTemplateId`) — best for "am I progressing on X" across time.
- `get-routines` / `get-routine-folders` — the planned program + target rep ranges + rest times + Leo's form notes.

Key exercise template IDs (Wider Shoulders program): Lateral Raise (Cable)
`BE289E45`, Lat Pulldown (Cable) `6A6C31A5`, Seated Shoulder Press (Machine)
`9237BAD1`, Incline Bench (DB) `07B38369`, Face Pull `BE640BA0`.

## What to produce

A checkpoint with these sections, each stating **the trend**, not just the number:

1. **Training** — sessions trained (from `Activity Records`), which program days, and per-exercise progression from Hevy: did weight/reps/volume go up vs the previous time he did that lift? Flag any exercise hitting the **top of its rep range** → ready to add load. Tally weekly **side-delt** and **back** set volume vs targets.
2. **Protein** — days logged in the period, and of those, how many hit ≥180 g. Report both the consistency (how often he logs at all — his weak spot) and the average on logged days. Don't average over unlogged days.
3. **Weight vs goal** — trend slope over the period. Is it dropping at roughly 0.5 kg/week? Use `body_composition.weight` (and `body_fat_pct` / `muscle_mass` if moving). Weight is noisy day-to-day — read the slope across the whole window, not endpoints.
4. **Recovery** — resting HR + HRV trend; flag divergences (RHR spike + HRV dip = an off day, usually tracks a bad night). Sleep only roughly (see note above).

End with a short, honest **TL;DR**: what's going well, and the one thing to fix.
Adherence (training frequency + food logging) is the usual gap — say so plainly.

## Common mistakes

- Reporting Hevy workout count as sessions trained → **wrong**, use `Activity Records`.
- Averaging protein over calendar days instead of *logged* days → tanks the number and hides the real signal (consistency of logging).
- Judging weight off first-vs-last reading → noise. Use the slope.
- Over-precisioning sleep → the data is duplicated and includes naps.
- Un-time-bounded InfluxQL → full-table scans hit the parquet query-file limit and error.
