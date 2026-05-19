from datetime import date

from scale.measurement.model.body_composition import BodyComposition
from scale.measurement.model.measurement import Measurement
from scale.measurement.model.user_profile import UserProfile


def calculate_body_composition(
    measurement: Measurement,
    profile: UserProfile,
    reference_date: date | None = None,
) -> BodyComposition:
    if reference_date is None:
        reference_date = measurement.timestamp.date()

    weight = measurement.weight_kg
    height = profile.height_cm
    age = profile.age_at(reference_date)
    is_male = profile.sex == "male"
    impedance = measurement.impedance

    bmi = weight / ((height / 100) ** 2)

    if impedance is not None and impedance > 0:
        lbm_coeff = _lbm_coefficient(weight, height, age, impedance)
        body_fat_pct = _body_fat(weight, height, age, is_male, lbm_coeff)
        water_pct = _water(body_fat_pct)
        bone_mass = _bone_mass(is_male, lbm_coeff)
        muscle_pct = _muscle_pct(height, age, is_male, impedance, weight)
        muscle_mass = muscle_pct / 100.0 * weight
        protein_pct = _protein(muscle_pct, water_pct)
        visceral_fat = _visceral_fat(weight, height, age, is_male)
        bmr = _bmr(weight, height, age, is_male)
        metabolic_age = _metabolic_age(bmr, age)
    else:
        body_fat_pct = 0.0
        water_pct = 0.0
        bone_mass = 0.0
        muscle_mass = 0.0
        protein_pct = 0.0
        visceral_fat = 0.0
        bmr = _bmr(weight, height, age, is_male)
        metabolic_age = age

    ideal_weight = 22.0 * ((height / 100) ** 2)
    body_type = _body_type(body_fat_pct, muscle_mass / weight * 100 if weight > 0 else 0)

    return BodyComposition(
        weight_kg=round(weight, 2),
        bmi=round(bmi, 6),
        body_fat_pct=round(body_fat_pct, 6),
        water_pct=round(water_pct, 6),
        muscle_mass_kg=round(muscle_mass, 2),
        bone_mass_kg=round(bone_mass, 7),
        protein_pct=round(protein_pct, 2),
        visceral_fat=round(visceral_fat, 6),
        bmr_kcal=round(bmr, 2),
        metabolic_age=metabolic_age,
        ideal_weight_kg=round(ideal_weight, 2),
        body_type=body_type,
        heart_rate=measurement.heart_rate,
        impedance=measurement.impedance,
    )


def _lbm_coefficient(weight: float, height: float, age: float, impedance: float) -> float:
    return (height * 9.058 / 100) * (height / 100) + weight * 0.32 + 12.226 - impedance * 0.0068 - age * 0.0542


def _body_fat(weight: float, height: float, age: float, is_male: bool, lbm_coeff: float) -> float:  # noqa: FBT001
    if not is_male and age <= 49:  # noqa: PLR2004
        lbm_sub = 9.25
    elif not is_male:
        lbm_sub = 7.25
    else:
        lbm_sub = 0.8

    if is_male and weight < 61:  # noqa: PLR2004
        coeff = 0.98
    elif not is_male and weight > 60:  # noqa: PLR2004
        coeff = 0.96
        if height > 160:  # noqa: PLR2004
            coeff *= 1.03
    elif not is_male and weight < 50:  # noqa: PLR2004
        coeff = 1.02
        if height > 160:  # noqa: PLR2004
            coeff *= 1.03
    else:
        coeff = 1.0

    fat_pct = (1.0 - (((lbm_coeff - lbm_sub) * coeff) / weight)) * 100
    return max(0.0, min(fat_pct, 75.0))


def _water(body_fat_pct: float) -> float:
    raw = (100 - body_fat_pct) * 0.7
    coeff = 1.02 if raw < 50 else 0.98  # noqa: PLR2004
    return raw * coeff


def _bone_mass(is_male: bool, lbm_coeff: float) -> float:  # noqa: FBT001
    base = 0.18016894 if is_male else 0.245691014
    bone = (base - lbm_coeff * 0.05158) * -1

    if bone > 2.2:  # noqa: PLR2004
        bone += 0.1
    else:
        bone -= 0.1

    if (is_male and bone > 5.1) or (not is_male and bone > 5.2):  # noqa: PLR2004
        bone = 8.0

    return max(0.5, bone)


def _muscle_pct(height: float, age: float, is_male: bool, impedance: float, weight: float) -> float:  # noqa: FBT001
    sex_val = 1.0 if is_male else 0.0
    if impedance > 0:
        h_m = height / 100.0
        smm = 0.401 * ((h_m * h_m * 10000) / impedance) + 3.825 * sex_val - 0.071 * age + 5.102
        pct = (smm / weight) * 100
        return max(10.0, min(pct, 60.0))
    return 52.0 if is_male else 46.0


def _protein(muscle_pct: float, water_pct: float) -> float:
    return max(0.0, muscle_pct - water_pct)


def _visceral_fat(weight: float, height: float, age: float, is_male: bool) -> float:  # noqa: FBT001
    if is_male:
        if height < weight * 1.6:
            subcalc = ((height * 0.4) - (height * (height * 0.0826))) * -1.0
            vf = ((weight * 305.0) / (subcalc + 48.0)) - 2.9 + (age * 0.15)
        else:
            subcalc = 0.765 + height * -0.0015
            vf = (((height * 0.143) - (weight * subcalc)) * -1.0) + (age * 0.15) - 5.0
    elif weight > (13.0 - (height * 0.5)) * -1.0:
        subsubcalc = ((height * 1.45) + (height * 0.1158) * height) - 120.0
        subcalc = weight * 500.0 / subsubcalc
        vf = (subcalc - 6.0) + (age * 0.07)
    else:
        subcalc = 0.691 + (height * -0.0024) + (height * -0.0024)
        vf = (((height * 0.027) - (subcalc * weight)) * -1.0) + (age * 0.07) - age

    return max(0.0, vf)


def _bmr(weight: float, height: float, age: float, is_male: bool) -> float:  # noqa: FBT001
    if is_male:
        return 10 * weight + 6.25 * height - 5 * age + 5
    return 10 * weight + 6.25 * height - 5 * age - 161


def _metabolic_age(bmr: float, age: int) -> int:
    if bmr <= 0:
        return age
    base_bmr = 20.0 * bmr / _bmr(70, 170, 20, True)  # noqa: FBT003
    return max(15, min(80, round(base_bmr)))


def _body_type(fat_pct: float, muscle_pct: float) -> int:
    if fat_pct < 15:  # noqa: PLR2004
        fat_level = 0
    elif fat_pct < 25:  # noqa: PLR2004
        fat_level = 1
    else:
        fat_level = 2

    if muscle_pct < 30:  # noqa: PLR2004
        muscle_level = 0
    elif muscle_pct < 40:  # noqa: PLR2004
        muscle_level = 1
    else:
        muscle_level = 2

    return fat_level * 3 + muscle_level + 1
