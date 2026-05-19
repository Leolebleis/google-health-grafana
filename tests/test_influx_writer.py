from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from scale.measurement.model.body_composition import BodyComposition
from scale.measurement.model.measurement import Measurement
from scale.measurement.persistence.influx_writer import InfluxWriter


def _make_body_composition() -> BodyComposition:
    return BodyComposition(
        weight_kg=70.0,
        bmi=22.9,
        body_fat_pct=18.0,
        water_pct=60.0,
        muscle_mass_kg=35.0,
        bone_mass_kg=3.0,
        protein_pct=16.0,
        visceral_fat=8.0,
        bmr_kcal=1700.0,
        metabolic_age=30,
        ideal_weight_kg=68.0,
        body_type=5,
        heart_rate=72,
        impedance=490.0,
    )


def _make_measurement() -> Measurement:
    return Measurement(
        weight_kg=70.0,
        impedance=490.0,
        heart_rate=72,
        timestamp=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
    )


@patch("scale.measurement.persistence.influx_writer.InfluxDBClient3")
def test_persist_calls_write(mock_client_cls: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    writer = InfluxWriter(
        url="http://localhost:8181",
        token="token",
        database="health",
        user="alice",
        measurement_name="body_composition",
    )

    writer.persist(_make_measurement(), _make_body_composition())

    mock_client.write.assert_called_once()
    call_kwargs = mock_client.write.call_args
    assert call_kwargs.kwargs["record"] is not None


@patch("scale.measurement.persistence.influx_writer.InfluxDBClient3")
def test_close_calls_client_close(mock_client_cls: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    writer = InfluxWriter(
        url="http://localhost:8181",
        token="token",
        database="health",
        user="alice",
    )
    writer.close()

    mock_client.close.assert_called_once()


@patch("scale.measurement.persistence.influx_writer.InfluxDBClient3")
def test_influx_client_constructed_with_correct_args(mock_client_cls: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    InfluxWriter(
        url="http://influx-host:8181",
        token="tok",
        database="mydb",
        user="bob",
    )

    mock_client_cls.assert_called_once_with(host="http://influx-host:8181", token="tok", database="mydb")
