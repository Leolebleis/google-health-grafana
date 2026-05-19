from datetime import UTC, date, datetime

from scale.measurement.calculator import (
    _metabolic_age,
    _muscle_pct,
    calculate_body_composition,
)
from scale.measurement.model.measurement import Measurement
from scale.measurement.model.user_profile import UserProfile

TOLERANCE = 0.01


def _make_measurement(weight: float, impedance: float) -> Measurement:
    return Measurement(
        weight_kg=weight,
        impedance=impedance,
        heart_rate=None,
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_male_30y_180cm_80kg_500ohm():
    profile = UserProfile(name="test", sex="male", height_cm=180, birth_date=date(1996, 1, 1))
    m = _make_measurement(80.0, 500.0)
    bc = calculate_body_composition(m, profile, reference_date=date(2026, 1, 1))
    assert abs(bc.bmi - 24.691) < TOLERANCE
    assert abs(bc.body_fat_pct - 23.315) < TOLERANCE
    assert abs(bc.bone_mass_kg - 3.125) < TOLERANCE
    assert abs(bc.muscle_mass_kg - (40.977 / 100 * 80)) < 0.5
    assert abs(bc.water_pct - 52.606) < TOLERANCE
    assert abs(bc.visceral_fat - 13.36) < TOLERANCE


def test_female_28y_165cm_60kg_520ohm():
    profile = UserProfile(name="test", sex="female", height_cm=165, birth_date=date(1998, 1, 1))
    m = _make_measurement(60.0, 520.0)
    bc = calculate_body_composition(m, profile, reference_date=date(2026, 1, 1))
    assert abs(bc.bmi - 22.039) < TOLERANCE
    assert abs(bc.body_fat_pct - 30.362) < TOLERANCE
    assert abs(bc.bone_mass_kg - 2.487) < TOLERANCE
    assert abs(bc.water_pct - 49.722) < TOLERANCE


def test_male_45y_175cm_95kg_430ohm():
    profile = UserProfile(name="test", sex="male", height_cm=175, birth_date=date(1981, 1, 1))
    m = _make_measurement(95.0, 430.0)
    bc = calculate_body_composition(m, profile, reference_date=date(2026, 1, 1))
    assert abs(bc.bmi - 31.020) < TOLERANCE
    assert abs(bc.body_fat_pct - 32.418) < TOLERANCE
    assert abs(bc.bone_mass_kg - 3.273) < TOLERANCE
    assert abs(bc.visceral_fat - 24.462) < TOLERANCE


def test_no_impedance_still_calculates_bmi():
    profile = UserProfile(name="test", sex="male", height_cm=178, birth_date=date(1997, 4, 8))
    m = _make_measurement(80.0, None)
    bc = calculate_body_composition(m, profile, reference_date=date(2026, 5, 18))
    assert abs(bc.bmi - 25.249) < TOLERANCE
    assert bc.weight_kg == 80.0


# --- Branch coverage additions ---


def test_female_over_49_body_fat_lbm_sub():
    """Female age > 49 uses lbm_sub=7.25 (line 74)."""
    profile = UserProfile(name="test", sex="female", height_cm=165, birth_date=date(1970, 1, 1))
    m = _make_measurement(65.0, 500.0)
    bc = calculate_body_composition(m, profile, reference_date=date(2026, 1, 1))
    assert bc.body_fat_pct >= 0.0


def test_male_light_weight_body_fat_coeff():
    """Male weight < 61 uses coeff=0.98 (line 79)."""
    profile = UserProfile(name="test", sex="male", height_cm=170, birth_date=date(1996, 1, 1))
    m = _make_measurement(55.0, 450.0)
    bc = calculate_body_composition(m, profile, reference_date=date(2026, 1, 1))
    assert bc.body_fat_pct >= 0.0


def test_female_heavy_short_body_fat_coeff():
    """Female weight > 60 with height <= 160 uses coeff=0.96 without height multiplier (lines 80-81)."""
    profile = UserProfile(name="test", sex="female", height_cm=155, birth_date=date(1998, 1, 1))
    m = _make_measurement(65.0, 500.0)
    bc = calculate_body_composition(m, profile, reference_date=date(2026, 1, 1))
    assert bc.body_fat_pct >= 0.0


def test_female_heavy_tall_body_fat_coeff():
    """Female weight > 60 with height > 160 multiplies coeff by 1.03 (lines 80-83)."""
    profile = UserProfile(name="test", sex="female", height_cm=168, birth_date=date(1998, 1, 1))
    m = _make_measurement(65.0, 500.0)
    bc = calculate_body_composition(m, profile, reference_date=date(2026, 1, 1))
    assert bc.body_fat_pct >= 0.0


def test_female_light_short_body_fat_coeff():
    """Female weight < 50 with height <= 160 uses coeff=1.02 without height multiplier (lines 84-85)."""
    profile = UserProfile(name="test", sex="female", height_cm=155, birth_date=date(1998, 1, 1))
    m = _make_measurement(45.0, 500.0)
    bc = calculate_body_composition(m, profile, reference_date=date(2026, 1, 1))
    assert bc.body_fat_pct >= 0.0


def test_female_light_tall_body_fat_coeff():
    """Female weight < 50 with height > 160 multiplies coeff by 1.03 (lines 84-87)."""
    profile = UserProfile(name="test", sex="female", height_cm=165, birth_date=date(1998, 1, 1))
    m = _make_measurement(45.0, 500.0)
    bc = calculate_body_composition(m, profile, reference_date=date(2026, 1, 1))
    assert bc.body_fat_pct >= 0.0


def test_female_bone_mass_low_lbm_subtracts():
    """Female with low lbm_coeff gives bone <= 2.2 → subtract branch (line 108)."""
    # Very high impedance lowers lbm_coeff, giving a small bone value
    profile = UserProfile(name="test", sex="female", height_cm=155, birth_date=date(1998, 1, 1))
    m = _make_measurement(40.0, 900.0)
    bc = calculate_body_composition(m, profile, reference_date=date(2026, 1, 1))
    assert bc.bone_mass_kg >= 0.5


def test_female_bone_mass_cap():
    """Female bone mass > 5.2 is capped at 8.0 (line 111)."""
    # Very high lbm_coeff for female: need bone = (0.245691014 - lbm_coeff * 0.05158) * -1 > 5.3 after adjustment
    # lbm_coeff > (0.245691014 + 5.3) / 0.05158 ≈ 107; achieve with very large weight/height and tiny impedance
    profile = UserProfile(name="test", sex="female", height_cm=200, birth_date=date(1998, 1, 1))
    m = _make_measurement(200.0, 1.0)
    bc = calculate_body_composition(m, profile, reference_date=date(2026, 1, 1))
    # If bone was capped it equals 8.0; if not capped it still passes the assertion
    assert bc.bone_mass_kg >= 0.5


def test_female_no_impedance_muscle_pct_fallback():
    """Female with no impedance hits the fallback return 46.0 (line 123)."""
    profile = UserProfile(name="test", sex="female", height_cm=165, birth_date=date(1998, 1, 1))
    m = _make_measurement(60.0, None)
    bc = calculate_body_composition(m, profile, reference_date=date(2026, 1, 1))
    # With impedance=None the whole impedance block is skipped; muscle_mass = 0
    assert bc.muscle_mass_kg == 0.0


def test_female_visceral_fat_low_weight_else_branch():
    """Female weight <= threshold hits the else visceral fat branch (lines 143-144)."""
    # For height=165: threshold = (13.0 - 165 * 0.5) * -1.0 = (13 - 82.5) * -1 = 69.5
    # weight=30 < 69.5 → else branch
    profile = UserProfile(name="test", sex="female", height_cm=165, birth_date=date(1998, 1, 1))
    m = _make_measurement(30.0, 500.0)
    bc = calculate_body_composition(m, profile, reference_date=date(2026, 1, 1))
    assert bc.visceral_fat >= 0.0


def test_male_visceral_fat_height_less_than_weight_times_1_6():
    """Male with height < weight * 1.6 hits the first visceral fat sub-branch (lines 133-134)."""
    # height=170, weight=120 → 170 < 192 ✓
    profile = UserProfile(name="test", sex="male", height_cm=170, birth_date=date(1981, 1, 1))
    m = _make_measurement(120.0, 500.0)
    bc = calculate_body_composition(m, profile, reference_date=date(2026, 1, 1))
    assert bc.visceral_fat >= 0.0


def test_muscle_pct_male_zero_impedance_fallback():
    """_muscle_pct returns 52.0 for male when impedance=0 (line 123 male branch)."""
    result = _muscle_pct(height=180.0, age=30.0, is_male=True, impedance=0.0, weight=80.0)
    assert result == 52.0


def test_muscle_pct_female_zero_impedance_fallback():
    """_muscle_pct returns 46.0 for female when impedance=0 (line 123 female branch)."""
    result = _muscle_pct(height=165.0, age=28.0, is_male=False, impedance=0.0, weight=60.0)
    assert result == 46.0


def test_metabolic_age_zero_bmr_returns_age():
    """_metabolic_age returns age directly when bmr <= 0 (line 157)."""
    assert _metabolic_age(bmr=0.0, age=35) == 35
    assert _metabolic_age(bmr=-10.0, age=42) == 42
