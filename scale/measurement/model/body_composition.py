from dataclasses import dataclass


@dataclass(frozen=True)
class BodyComposition:
    weight_kg: float
    bmi: float
    body_fat_pct: float
    water_pct: float
    muscle_mass_kg: float
    bone_mass_kg: float
    protein_pct: float
    visceral_fat: float
    bmr_kcal: float
    metabolic_age: int
    ideal_weight_kg: float
    body_type: int
    heart_rate: int | None
    impedance: float | None
