from datetime import datetime

from influxdb_client_3 import Point

from scale.measurement.model.body_composition import BodyComposition


def to_influx_point(
    bc: BodyComposition,
    timestamp: datetime,
    user: str,
    measurement_name: str = "body_composition",
) -> Point:
    point = Point(measurement_name).tag("user", user).time(timestamp).field("weight", bc.weight_kg)

    float_fields = {
        "bmi": bc.bmi,
        "body_fat_pct": bc.body_fat_pct,
        "water_pct": bc.water_pct,
        "muscle_mass": bc.muscle_mass_kg,
        "bone_mass": bc.bone_mass_kg,
        "protein_pct": bc.protein_pct,
        "visceral_fat": bc.visceral_fat,
        "bmr": bc.bmr_kcal,
        "ideal_weight": bc.ideal_weight_kg,
        "impedance": bc.impedance,
    }
    int_fields = {
        "metabolic_age": bc.metabolic_age,
        "body_type": bc.body_type,
        "heart_rate": bc.heart_rate,
    }

    for name, value in float_fields.items():
        if value is not None:
            point = point.field(name, value)
    for name, int_value in int_fields.items():
        if int_value is not None:
            point = point.field(name, int(int_value))

    return point
