import json
from datetime import UTC, datetime
from unittest.mock import MagicMock

from scale.cloud_sync import (
    entry_timestamp,
    fetch_last_timestamp,
    filter_new,
    parse_entries,
    to_body_composition,
)

# Real shape of SmartScaleConnect's core.Weight json output (S400 via xiaomihome)
_ENTRY = {
    "Date": "2026-05-18T14:58:27Z",
    "Weight": 94.6,
    "BMI": 29.9,
    "BodyFat": 28.4,
    "BodyWater": 50.6,
    "BoneMass": 3.6,
    "MetabolicAge": 27,
    "MuscleMass": 64.1,
    "PhysiqueRating": 4,
    "ProteinMass": 15.4,
    "VisceralFat": 14,
    "BasalMetabolism": 1832,
    "BodyScore": 78,
    "HeartRate": 76,
    "Height": 178,
    "SkeletalMuscleMass": 35,
    "User": "Leo",
}


class TestParseEntries:
    def test_array(self):
        raw = json.dumps([_ENTRY, _ENTRY])
        assert len(parse_entries(raw)) == 2

    def test_non_array_returns_empty(self):
        assert parse_entries("42") == []
        assert parse_entries(json.dumps({"unexpected": "object"})) == []


class TestToBodyComposition:
    def test_maps_core_weight_fields(self):
        bc = to_body_composition(_ENTRY)
        assert bc.weight_kg == 94.6
        assert bc.bmi == 29.9
        assert bc.body_fat_pct == 28.4
        assert bc.water_pct == 50.6
        assert bc.muscle_mass_kg == 64.1
        assert bc.bone_mass_kg == 3.6
        assert bc.protein_pct == 16.3  # 15.4 kg / 94.6 kg
        assert bc.visceral_fat == 14.0
        assert bc.bmr_kcal == 1832.0
        assert bc.metabolic_age == 27
        assert bc.body_type == 4
        assert bc.heart_rate == 76
        assert bc.impedance is None

    def test_weight_only_entry_leaves_unmeasured_fields_none(self):
        bc = to_body_composition({"Date": "2026-05-19T09:17:12Z", "Weight": 94.1, "BMI": 29.7})
        assert bc.weight_kg == 94.1
        assert bc.bmi == 29.7
        assert bc.body_fat_pct is None
        assert bc.protein_pct is None
        assert bc.metabolic_age is None
        assert bc.heart_rate is None

    def test_zero_weight_avoids_division(self):
        bc = to_body_composition({"Date": "2026-05-19T09:17:12Z", "ProteinMass": 15.0})
        assert bc.protein_pct is None


class TestFilterNew:
    def test_no_previous_keeps_all_dated(self):
        entries = [_ENTRY, {"Weight": 90.0}]
        assert filter_new(entries, None) == [_ENTRY]

    def test_keeps_only_newer(self):
        older = {**_ENTRY, "Date": "2026-05-18T14:58:27Z"}
        newer = {**_ENTRY, "Date": "2026-07-04T08:00:00Z"}
        last = datetime(2026, 5, 18, 14, 58, 27, tzinfo=UTC)
        assert filter_new([older, newer], last) == [newer]


def test_entry_timestamp_is_utc():
    assert entry_timestamp(_ENTRY) == datetime(2026, 5, 18, 14, 58, 27, tzinfo=UTC)


class TestFetchLastTimestamp:
    def test_missing_table_returns_none(self):
        client = MagicMock()
        client.query.side_effect = RuntimeError("table not found")
        assert fetch_last_timestamp(client, "body_composition") is None

    def test_naive_result_becomes_utc(self):
        client = MagicMock()
        client.query.return_value.to_pylist.return_value = [{"last": datetime(2026, 7, 4, 18, 40)}]  # noqa: DTZ001
        result = fetch_last_timestamp(client, "body_composition")
        assert result == datetime(2026, 7, 4, 18, 40, tzinfo=UTC)

    def test_empty_table_returns_none(self):
        client = MagicMock()
        client.query.return_value.to_pylist.return_value = [{"last": None}]
        assert fetch_last_timestamp(client, "body_composition") is None
