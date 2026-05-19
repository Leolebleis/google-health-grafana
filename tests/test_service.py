from datetime import UTC, date, datetime
from unittest.mock import MagicMock

import pytest
from scale.measurement.model.measurement import Measurement
from scale.measurement.model.user_profile import UserProfile
from scale.measurement.service import MeasurementService


@pytest.fixture
def profile():
    return UserProfile(name="leo", sex="male", height_cm=178, birth_date=date(1997, 4, 8))


@pytest.fixture
def mock_dao():
    return MagicMock()


@pytest.fixture
def service(profile, mock_dao):
    return MeasurementService(profile=profile, dao=mock_dao)


def _make_measurement(weight=80.0, impedance=500.0, ts=None):
    return Measurement(
        weight_kg=weight,
        impedance=impedance,
        heart_rate=72,
        timestamp=ts or datetime(2026, 5, 18, 10, 0, 0, tzinfo=UTC),
    )


def test_process_measurement_calls_dao(service, mock_dao):
    m = _make_measurement()
    service.process(m)
    mock_dao.persist.assert_called_once()
    args = mock_dao.persist.call_args
    assert args[0][0] == m
    bc = args[0][1]
    assert bc.weight_kg == 80.0
    assert bc.bmi > 0


def test_dedup_within_window(service, mock_dao):
    m1 = _make_measurement(ts=datetime(2026, 5, 18, 10, 0, 0, tzinfo=UTC))
    m2 = _make_measurement(ts=datetime(2026, 5, 18, 10, 0, 15, tzinfo=UTC))
    service.process(m1)
    service.process(m2)
    assert mock_dao.persist.call_count == 1


def test_no_dedup_after_window(service, mock_dao):
    m1 = _make_measurement(ts=datetime(2026, 5, 18, 10, 0, 0, tzinfo=UTC))
    m2 = _make_measurement(ts=datetime(2026, 5, 18, 10, 1, 0, tzinfo=UTC))
    service.process(m1)
    service.process(m2)
    assert mock_dao.persist.call_count == 2
