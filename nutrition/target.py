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
        logged_days >= cfg.min_logged_days and weighins >= cfg.min_weighins and span_days >= cfg.min_weighin_span_days
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
