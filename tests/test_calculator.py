from datetime import date

from scale.measurement.model.measurement import Measurement
from scale.measurement.model.user_profile import UserProfile
from scale.measurement.calculator import calculate_body_composition

TOLERANCE = 0.01


def _make_measurement(weight: float, impedance: float) -> Measurement:
    from datetime import datetime, timezone

    return Measurement(
        weight_kg=weight,
        impedance=impedance,
        heart_rate=None,
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_male_30y_180cm_80kg_500ohm():
    profile = UserProfile(
        name="test", sex="male", height_cm=180, birth_date=date(1996, 1, 1)
    )
    m = _make_measurement(80.0, 500.0)
    bc = calculate_body_composition(m, profile, reference_date=date(2026, 1, 1))
    assert abs(bc.bmi - 24.691) < TOLERANCE
    assert abs(bc.body_fat_pct - 23.315) < TOLERANCE
    assert abs(bc.bone_mass_kg - 3.125) < TOLERANCE
    assert abs(bc.muscle_mass_kg - (40.977 / 100 * 80)) < 0.5
    assert abs(bc.water_pct - 52.606) < TOLERANCE
    assert abs(bc.visceral_fat - 13.36) < TOLERANCE


def test_female_28y_165cm_60kg_520ohm():
    profile = UserProfile(
        name="test", sex="female", height_cm=165, birth_date=date(1998, 1, 1)
    )
    m = _make_measurement(60.0, 520.0)
    bc = calculate_body_composition(m, profile, reference_date=date(2026, 1, 1))
    assert abs(bc.bmi - 22.039) < TOLERANCE
    assert abs(bc.body_fat_pct - 30.362) < TOLERANCE
    assert abs(bc.bone_mass_kg - 2.487) < TOLERANCE
    assert abs(bc.water_pct - 49.722) < TOLERANCE


def test_male_45y_175cm_95kg_430ohm():
    profile = UserProfile(
        name="test", sex="male", height_cm=175, birth_date=date(1981, 1, 1)
    )
    m = _make_measurement(95.0, 430.0)
    bc = calculate_body_composition(m, profile, reference_date=date(2026, 1, 1))
    assert abs(bc.bmi - 31.020) < TOLERANCE
    assert abs(bc.body_fat_pct - 32.418) < TOLERANCE
    assert abs(bc.bone_mass_kg - 3.273) < TOLERANCE
    assert abs(bc.visceral_fat - 24.462) < TOLERANCE


def test_no_impedance_still_calculates_bmi():
    profile = UserProfile(
        name="test", sex="male", height_cm=178, birth_date=date(1997, 4, 8)
    )
    m = _make_measurement(80.0, None)
    bc = calculate_body_composition(m, profile, reference_date=date(2026, 5, 18))
    assert abs(bc.bmi - 25.249) < TOLERANCE
    assert bc.weight_kg == 80.0
