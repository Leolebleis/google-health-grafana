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
