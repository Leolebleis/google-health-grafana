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


@patch("scale.measurement.persistence.influx_writer.InfluxDBClient")
def test_persist_calls_write_api(mock_client_cls: MagicMock) -> None:
    mock_client = MagicMock()
    mock_write_api = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.write_api.return_value = mock_write_api

    writer = InfluxWriter(
        url="http://localhost:8086",
        token="token",
        org="my-org",
        bucket="health",
        user="alice",
        measurement_name="body_composition",
    )

    bc = _make_body_composition()
    m = _make_measurement()
    writer.persist(m, bc)

    mock_write_api.write.assert_called_once()
    call_kwargs = mock_write_api.write.call_args
    assert call_kwargs.kwargs["bucket"] == "health"
    assert call_kwargs.kwargs["org"] == "my-org"
    assert call_kwargs.kwargs["record"] is not None


@patch("scale.measurement.persistence.influx_writer.InfluxDBClient")
def test_close_calls_client_close(mock_client_cls: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.write_api.return_value = MagicMock()

    writer = InfluxWriter(
        url="http://localhost:8086",
        token="token",
        org="my-org",
        bucket="health",
        user="alice",
    )
    writer.close()

    mock_client.close.assert_called_once()


@patch("scale.measurement.persistence.influx_writer.InfluxDBClient")
def test_influx_client_constructed_with_correct_args(mock_client_cls: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.write_api.return_value = MagicMock()

    InfluxWriter(
        url="http://influx:8086",
        token="tok",
        org="org",
        bucket="bucket",
        user="bob",
    )

    mock_client_cls.assert_called_once_with(url="http://influx:8086", token="tok", org="org")
