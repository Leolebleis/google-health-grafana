from datetime import UTC, datetime
from unittest.mock import MagicMock

from hevy.sync import build_muscle_map, fetch_new_workouts, workout_points

_TEMPLATES = [
    {"id": "D04AC939", "title": "Lateral Raise (Cable)", "primary_muscle_group": "shoulders"},
    {"id": "6A6C31A5", "title": "Lat Pulldown (Cable)", "primary_muscle_group": "lats"},
]

_WORKOUT = {
    "id": "w-1",
    "title": "Day 1 - Shoulders & Back Width",
    "start_time": "2026-07-06T18:00:00+00:00",
    "end_time": "2026-07-06T19:05:00+00:00",
    "exercises": [
        {
            "index": 0,
            "title": "Lateral Raise (Cable)",
            "exercise_template_id": "D04AC939",
            "sets": [
                {"index": 0, "type": "warmup", "weight_kg": 5.0, "reps": 15, "rpe": None},
                {"index": 1, "type": "normal", "weight_kg": 10.0, "reps": 15, "rpe": 8.5},
            ],
        },
        {
            "index": 1,
            "title": "Lat Pulldown (Cable)",
            "exercise_template_id": "6A6C31A5",
            "sets": [
                {"index": 0, "type": "normal", "weight_kg": 70.0, "reps": 10},
            ],
        },
    ],
}


def test_build_muscle_map():
    assert build_muscle_map(_TEMPLATES) == {"D04AC939": "shoulders", "6A6C31A5": "lats"}


class TestWorkoutPoints:
    def test_one_point_per_set_plus_summary(self):
        points = workout_points(_WORKOUT, build_muscle_map(_TEMPLATES))
        assert len(points) == 4  # 3 sets + 1 summary

    def test_set_point_fields_and_tags(self):
        points = workout_points(_WORKOUT, build_muscle_map(_TEMPLATES))
        line = points[1].to_line_protocol()
        assert "workout_set" in line
        assert "exercise=Lateral\\ Raise\\ (Cable)" in line
        assert "muscle_group=shoulders" in line
        assert "set=1" in line
        assert "weight_kg=10" in line
        assert "reps=10" not in line
        assert "reps=15i" in line
        assert "rpe=8.5" in line
        assert "volume_kg=150" in line

    def test_summary_point(self):
        points = workout_points(_WORKOUT, build_muscle_map(_TEMPLATES))
        line = points[-1].to_line_protocol()
        assert "workout," in line
        assert "duration_min=65" in line
        assert "set_count=3i" in line
        assert "exercise_count=2i" in line
        assert "volume_kg=925" in line  # 75 + 150 + 700

    def test_unknown_template_maps_to_unknown(self):
        workout = {**_WORKOUT, "exercises": [{**_WORKOUT["exercises"][0], "exercise_template_id": "nope"}]}
        points = workout_points(workout, build_muscle_map(_TEMPLATES))
        assert "muscle_group=unknown" in points[0].to_line_protocol()

    def test_reps_only_set_has_no_weight_or_volume(self):
        workout = {
            **_WORKOUT,
            "exercises": [
                {
                    "index": 0,
                    "title": "Ab Wheel",
                    "exercise_template_id": "x",
                    "sets": [{"index": 0, "type": "normal", "weight_kg": None, "reps": 12}],
                }
            ],
        }
        line = workout_points(workout, {})[0].to_line_protocol()
        assert "reps=12i" in line
        assert "weight_kg" not in line
        assert "volume_kg" not in line


class TestFetchNewWorkouts:
    def _client(self, pages: list[list[dict]]) -> MagicMock:
        client = MagicMock()
        client.workouts.side_effect = [(p, len(pages)) for p in pages]
        return client

    def test_no_watermark_fetches_all_pages(self):
        w1 = {**_WORKOUT, "start_time": "2026-07-06T18:00:00+00:00"}
        w2 = {**_WORKOUT, "start_time": "2026-07-01T18:00:00+00:00"}
        client = self._client([[w1], [w2]])
        assert len(fetch_new_workouts(client, None)) == 2

    def test_stops_at_stale_page(self):
        w1 = {**_WORKOUT, "start_time": "2026-07-06T18:00:00+00:00"}
        w2 = {**_WORKOUT, "start_time": "2026-07-01T18:00:00+00:00"}
        client = self._client([[w1], [w2]])
        last = datetime(2026, 7, 3, tzinfo=UTC)
        new = fetch_new_workouts(client, last)
        assert len(new) == 1
        assert new[0]["start_time"] == "2026-07-06T18:00:00+00:00"

    def test_empty_account(self):
        client = self._client([[]])
        client.workouts.side_effect = [([], 0)]
        assert fetch_new_workouts(client, None) == []
