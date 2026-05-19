from datetime import datetime

from influxdb_client import Point

from scale.measurement.model.body_composition import BodyComposition


def to_influx_point(
    bc: BodyComposition,
    timestamp: datetime,
    user: str,
    measurement_name: str = "body_composition",
) -> Point:
    point = (
        Point(measurement_name)
        .tag("user", user)
        .time(timestamp)
        .field("weight", bc.weight_kg)
        .field("bmi", bc.bmi)
        .field("body_fat_pct", bc.body_fat_pct)
        .field("water_pct", bc.water_pct)
        .field("muscle_mass", bc.muscle_mass_kg)
        .field("bone_mass", bc.bone_mass_kg)
        .field("protein_pct", bc.protein_pct)
        .field("visceral_fat", bc.visceral_fat)
        .field("bmr", bc.bmr_kcal)
        .field("metabolic_age", int(bc.metabolic_age))
        .field("ideal_weight", bc.ideal_weight_kg)
        .field("body_type", int(bc.body_type))
    )

    if bc.heart_rate is not None:
        point = point.field("heart_rate", int(bc.heart_rate))
    if bc.impedance is not None:
        point = point.field("impedance", bc.impedance)

    return point
