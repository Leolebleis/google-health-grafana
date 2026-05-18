import logging
from datetime import datetime

from scale.measurement.model.measurement import Measurement
from scale.measurement.model.user_profile import UserProfile
from scale.measurement.model.body_composition import BodyComposition
from scale.measurement.calculator import calculate_body_composition
from scale.measurement.dao import MeasurementDAO

log = logging.getLogger(__name__)


class MeasurementService:
    def __init__(
        self,
        profile: UserProfile,
        dao: MeasurementDAO,
        dedup_window_seconds: int = 30,
    ):
        self._profile = profile
        self._dao = dao
        self._dedup_window = dedup_window_seconds
        self._last_timestamp: datetime | None = None

    def process(self, measurement: Measurement) -> BodyComposition | None:
        if self._is_duplicate(measurement):
            log.debug("Duplicate measurement within dedup window, skipping")
            return None

        bc = calculate_body_composition(measurement, self._profile)

        self._dao.persist(measurement, bc)
        self._last_timestamp = measurement.timestamp

        log.info(
            "Recorded: %.1f kg, %.1f%% fat, %.1f kg muscle, HR=%s",
            bc.weight_kg,
            bc.body_fat_pct,
            bc.muscle_mass_kg,
            bc.heart_rate or "n/a",
        )
        return bc

    def _is_duplicate(self, measurement: Measurement) -> bool:
        if self._last_timestamp is None:
            return False
        delta = abs((measurement.timestamp - self._last_timestamp).total_seconds())
        return delta < self._dedup_window
