from datetime import UTC, datetime

from scale.measurement.model.body_composition import BodyComposition
from scale.measurement.persistence.influx_mapper import to_influx_point


def test_maps_all_fields():
    bc = BodyComposition(
        weight_kg=80.0,
        bmi=25.2,
        body_fat_pct=23.3,
        water_pct=52.6,
        muscle_mass_kg=32.8,
        bone_mass_kg=3.1,
        protein_pct=18.5,
        visceral_fat=13.4,
        bmr_kcal=1780.0,
        metabolic_age=28,
        ideal_weight_kg=69.6,
        body_type=5,
        heart_rate=72,
        impedance=500.0,
    )
    ts = datetime(2026, 5, 18, 10, 0, 0, tzinfo=UTC)
    point = to_influx_point(bc, ts, user="leo", measurement_name="body_composition")

    line = point.to_line_protocol()
    assert "body_composition" in line
    assert "user=leo" in line
    assert "weight=80" in line
    assert "heart_rate=72i" in line
    assert "body_type=5i" in line


def test_skips_unmeasured_fields():
    bc = BodyComposition(
        weight_kg=94.1,
        bmi=29.7,
        body_fat_pct=None,
        water_pct=None,
        muscle_mass_kg=None,
        bone_mass_kg=None,
        protein_pct=None,
        visceral_fat=None,
        bmr_kcal=None,
        metabolic_age=None,
        ideal_weight_kg=None,
        body_type=None,
        heart_rate=None,
        impedance=None,
    )
    ts = datetime(2026, 5, 19, 9, 17, 12, tzinfo=UTC)
    line = to_influx_point(bc, ts, user="leo").to_line_protocol()

    assert "weight=94.1" in line
    assert "bmi=29.7" in line
    assert "body_fat_pct" not in line
    assert "heart_rate" not in line
    assert "metabolic_age" not in line
