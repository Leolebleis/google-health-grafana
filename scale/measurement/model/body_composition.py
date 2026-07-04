from dataclasses import dataclass


@dataclass(frozen=True)
class BodyComposition:
    """Weight is always measured; None elsewhere means the source didn't provide it."""

    weight_kg: float
    bmi: float | None
    body_fat_pct: float | None
    water_pct: float | None
    muscle_mass_kg: float | None
    bone_mass_kg: float | None
    protein_pct: float | None
    visceral_fat: float | None
    bmr_kcal: float | None
    metabolic_age: int | None
    ideal_weight_kg: float | None
    body_type: int | None
    heart_rate: int | None
    impedance: float | None
